"""用户认证系统单元测试（全 mock/内存 SQLite，不依赖真实网络）。

测试策略：
- 用 in-memory SQLite 隔离测试数据，每个 test 拿到干净 session
- FastAPI TestClient 覆盖 HTTP 层面的行为
- 不测 bcrypt 本身（信任库），测的是我们的逻辑是否正确使用它
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from sqlalchemy.pool import StaticPool

from app.core.auth import create_token, decode_token, hash_password, verify_password
from app.db import Base, get_session
from app.main import create_app


# ---------- 测试夹具 ----------

@pytest.fixture(scope="function")
def db_session():
    """每个测试函数独立的 in-memory SQLite session。

    必须用 StaticPool：SQLite in-memory DB 是"每连接独立"的——
    连接池开新连接时会得到空 DB，表不存在。StaticPool 强制所有操作共享同一连接。
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # 导入 model 让 metadata 注册表结构，再在测试引擎上建表
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
    """注入测试 session 的 TestClient。"""
    app = create_app()

    def _override_session():
        try:
            yield db_session
        finally:
            pass

    from app.db import get_session
    app.dependency_overrides[get_session] = _override_session
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c
    app.dependency_overrides.clear()


# ---------- 密码哈希单元测试 ----------

def test_hash_and_verify_correct_password():
    hashed = hash_password("mypassword")
    assert verify_password("mypassword", hashed) is True


def test_verify_wrong_password():
    hashed = hash_password("correct")
    assert verify_password("wrong", hashed) is False


def test_hash_is_not_plain():
    plain = "secret123"
    hashed = hash_password(plain)
    assert hashed != plain
    assert hashed.startswith("$2b$")


# ---------- JWT 单元测试 ----------

def test_create_and_decode_token():
    token = create_token(42)
    assert decode_token(token) == 42


def test_decode_invalid_token_returns_none():
    assert decode_token("not.a.jwt") is None


def test_decode_tampered_token_returns_none():
    token = create_token(1)
    # 篡改最后一个字符
    tampered = token[:-1] + ("X" if token[-1] != "X" else "Y")
    assert decode_token(tampered) is None


# ---------- 注册端点测试 ----------

def test_register_success(client):
    resp = client.post("/api/auth/register", json={"username": "alice", "password": "pass123"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["username"] == "alice"
    assert "access_token" in body
    assert body["token_type"] == "bearer"


def test_register_duplicate_username(client):
    client.post("/api/auth/register", json={"username": "bob", "password": "pass123"})
    resp = client.post("/api/auth/register", json={"username": "bob", "password": "other123"})
    assert resp.status_code == 409


def test_register_username_too_short(client):
    resp = client.post("/api/auth/register", json={"username": "a", "password": "pass123"})
    assert resp.status_code == 422


def test_register_password_too_short(client):
    resp = client.post("/api/auth/register", json={"username": "charlie", "password": "12"})
    assert resp.status_code == 422


# ---------- 登录端点测试 ----------

def test_login_success(client):
    client.post("/api/auth/register", json={"username": "dave", "password": "mypassword"})
    resp = client.post("/api/auth/login", json={"username": "dave", "password": "mypassword"})
    assert resp.status_code == 200
    body = resp.json()
    assert "access_token" in body
    assert body["username"] == "dave"


def test_login_wrong_password(client):
    client.post("/api/auth/register", json={"username": "eve", "password": "correct"})
    resp = client.post("/api/auth/login", json={"username": "eve", "password": "wrong"})
    assert resp.status_code == 401


def test_login_nonexistent_user(client):
    resp = client.post("/api/auth/login", json={"username": "ghost", "password": "any"})
    assert resp.status_code == 401


# ---------- /me 端点测试 ----------

def test_me_with_valid_token(client):
    reg = client.post("/api/auth/register", json={"username": "frank", "password": "pass123"})
    token = reg.json()["access_token"]
    resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["username"] == "frank"
    assert body["github_username"] is None


def test_me_without_token(client):
    resp = client.get("/api/auth/me")
    assert resp.status_code == 401


def test_me_with_invalid_token(client):
    resp = client.get("/api/auth/me", headers={"Authorization": "Bearer invalid.token.here"})
    assert resp.status_code == 401
