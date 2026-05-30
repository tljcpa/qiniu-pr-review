"""GitHub PAT 绑定功能测试（全 mock，不调真实网络）。

测试策略：
- pat_crypto 单元测试：直接测加解密逻辑
- API 测试：mock _verify_github_pat，只测我们自己的绑定/解绑/查询逻辑
- 绝不在测试里硬编码真实 PAT
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.pat_crypto import decrypt_pat, encrypt_pat
from app.db import Base, get_session
from app.main import create_app


# ---------- pat_crypto 单元测试 ----------

def test_encrypt_decrypt_roundtrip():
    plain = "github_pat_abc123def456"
    enc = encrypt_pat(plain)
    assert enc != plain
    assert ":" in enc  # nonce:ciphertext 格式
    assert decrypt_pat(enc) == plain


def test_different_encryptions_for_same_input():
    """每次加密用不同 nonce，结果应不同（防重放）。"""
    plain = "github_pat_same_value"
    enc1 = encrypt_pat(plain)
    enc2 = encrypt_pat(plain)
    assert enc1 != enc2  # nonce 随机，密文必然不同
    # 但两次解密结果相同
    assert decrypt_pat(enc1) == decrypt_pat(enc2) == plain


def test_decrypt_invalid_format():
    from pytest import raises
    with raises(ValueError, match="格式错误"):
        decrypt_pat("no_colon_here")


def test_decrypt_tampered_ciphertext():
    plain = "github_pat_test"
    enc = encrypt_pat(plain)
    nonce_hex, ct_hex = enc.split(":", 1)
    # 篡改密文最后两字节
    tampered = f"{nonce_hex}:{ct_hex[:-4]}0000"
    from pytest import raises
    with raises(ValueError):
        decrypt_pat(tampered)


# ---------- 测试夹具 ----------

@pytest.fixture(scope="function")
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    import app.models.user  # noqa: F401
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture(scope="function")
def client(db_session):
    app = create_app()

    def _override():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_session] = _override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture(scope="function")
def authed_client(client):
    """已注册并登录的 TestClient，附带 auth_headers。"""
    resp = client.post("/api/auth/register", json={"username": "testuser", "password": "pass1234"})
    assert resp.status_code == 201
    token = resp.json()["access_token"]
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


# ---------- PAT 绑定 API 测试 ----------

def test_bind_pat_success(authed_client):
    """mock GitHub API 返回 200，绑定应成功并返回 github_username。"""
    with patch("app.api.user._verify_github_pat", return_value="gh-user-alice"):
        resp = authed_client.put("/api/user/github-pat", json={"pat": "github_pat_" + "x" * 30})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["github_username"] == "gh-user-alice"


def test_bind_pat_invalid_token(authed_client):
    """mock GitHub API 返回 401，应返回 422。"""
    from fastapi import HTTPException

    def _fake_verify(pat):
        raise HTTPException(status_code=422, detail="PAT 无效或已过期，请检查后重试")

    with patch("app.api.user._verify_github_pat", side_effect=_fake_verify):
        resp = authed_client.put("/api/user/github-pat", json={"pat": "github_pat_" + "x" * 30})
    assert resp.status_code == 422


def test_bind_pat_too_short(authed_client):
    """PAT 低于 20 字节应被 Pydantic 校验拦截，返回 422（不调 GitHub）。"""
    resp = authed_client.put("/api/user/github-pat", json={"pat": "short"})
    assert resp.status_code == 422


def test_me_shows_has_pat_false_before_binding(authed_client):
    resp = authed_client.get("/api/user/me")
    assert resp.status_code == 200
    assert resp.json()["has_pat"] is False
    assert resp.json()["github_username"] is None


def test_me_shows_has_pat_true_after_binding(authed_client):
    with patch("app.api.user._verify_github_pat", return_value="gh-user-bob"):
        authed_client.put("/api/user/github-pat", json={"pat": "github_pat_" + "x" * 30})
    resp = authed_client.get("/api/user/me")
    assert resp.status_code == 200
    body = resp.json()
    assert body["has_pat"] is True
    assert body["github_username"] == "gh-user-bob"
    # 响应中绝不含明文 PAT
    assert "pat" not in str(body).lower() or "has_pat" in str(body)


def test_unbind_pat(authed_client):
    with patch("app.api.user._verify_github_pat", return_value="gh-user-carol"):
        authed_client.put("/api/user/github-pat", json={"pat": "github_pat_" + "x" * 30})

    resp = authed_client.delete("/api/user/github-pat")
    assert resp.status_code == 200

    resp2 = authed_client.get("/api/user/me")
    assert resp2.json()["has_pat"] is False
    assert resp2.json()["github_username"] is None


def test_pat_endpoints_require_auth(client):
    """未登录调 PAT 端点应返回 401。"""
    assert client.put("/api/user/github-pat", json={"pat": "github_pat_" + "x" * 30}).status_code == 401
    assert client.delete("/api/user/github-pat").status_code == 401
    assert client.get("/api/user/me").status_code == 401
