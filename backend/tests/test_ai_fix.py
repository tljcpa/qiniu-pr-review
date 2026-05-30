"""AI 改码闭环单元测试（全 mock，不调真实 how88/DeepSeek/GitHub）。

测试策略：
- 服务层：mock call_how88 / call_deepseek / open_pr，测管线逻辑分支
- API 层：mock 服务层整体，测 HTTP 状态码与认证守卫
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_session
from app.main import create_app
from app.models.finding import Confidence, Finding, Severity
from app.services.ai_fix_service import (
    FixResult,
    _apply_unified_diff,
    _parse_patch_from_response,
    run_fix_pipeline,
)

# ---------- 单元：补丁解析 ----------

def test_parse_patch_success():
    response = """EXPLANATION:
Fixed the null check.

PATCH:
```diff
--- a/foo.py
+++ b/foo.py
@@ -1,3 +1,4 @@
 def foo():
-    return x
+    if x is None:
+        return 0
+    return x
```"""
    patch = _parse_patch_from_response(response)
    assert patch is not None
    assert "if x is None" in patch


def test_parse_patch_cannot_fix():
    response = "CANNOT_FIX:\nThis is an architectural issue."
    assert _parse_patch_from_response(response) is None


def test_parse_patch_no_diff():
    assert _parse_patch_from_response("no diff here at all") is None


# ---------- 单元：unified diff 应用 ----------

def test_apply_unified_diff_basic():
    original = "line1\nline2\nline3\n"
    patch = """--- a/f.py
+++ b/f.py
@@ -1,3 +1,3 @@
 line1
-line2
+line2_fixed
 line3
"""
    result = _apply_unified_diff(original, patch)
    assert "line2_fixed" in result
    assert "line2\n" not in result


def test_apply_unified_diff_no_hunks():
    original = "unchanged\n"
    result = _apply_unified_diff(original, "no hunk here")
    assert result == original


# ---------- 服务层管线测试 ----------

def _make_finding() -> Finding:
    return Finding(
        file="src/main.py",
        line_hint="42",
        severity=Severity.HIGH,
        title="Null pointer dereference",
        detail="Variable x may be None",
        suggestion="Add null check before use",
        confidence=Confidence.HIGH,
    )


FAKE_PATCH = """--- a/src/main.py
+++ b/src/main.py
@@ -40,3 +40,5 @@
 def process(x):
-    return x.value
+    if x is None:
+        return 0
+    return x.value
"""


def test_pipeline_approved():
    """how88 生成补丁 + DeepSeek approve → 状态为 approved，有 pr_url。"""
    finding = _make_finding()

    with (
        patch("app.services.ai_fix_service.call_how88_for_patch", return_value=f"PATCH:\n```diff\n{FAKE_PATCH}\n```"),
        patch("app.services.ai_fix_service.call_deepseek_for_review", return_value={
            "verdict": "approve", "confidence": "high", "reason": "Correct fix.", "concerns": []
        }),
        patch("app.services.ai_fix_service.open_pr_with_user_pat", return_value="https://github.com/u/r/pull/99"),
    ):
        result = run_fix_pipeline(
            finding=finding,
            diff_context="@@ -40 +40 @@ def process(x):\n-    return x.value",
            owner="u", repo="r", base_ref="main", head_sha="abc123",
            user_pat="github_pat_fake",
            review_id="testrev001", finding_index=0,
        )

    assert result.status == "approved"
    assert result.pr_url == "https://github.com/u/r/pull/99"
    assert result.patch is not None


def test_pipeline_rejected_by_deepseek():
    """DeepSeek reject → 状态为 rejected，无 pr_url，不开 PR。"""
    finding = _make_finding()

    with (
        patch("app.services.ai_fix_service.call_how88_for_patch", return_value=f"PATCH:\n```diff\n{FAKE_PATCH}\n```"),
        patch("app.services.ai_fix_service.call_deepseek_for_review", return_value={
            "verdict": "reject", "confidence": "high", "reason": "Introduces new bug.", "concerns": ["breaks edge case"]
        }),
        patch("app.services.ai_fix_service.open_pr_with_user_pat") as mock_open_pr,
    ):
        result = run_fix_pipeline(
            finding=finding,
            diff_context="diff",
            owner="u", repo="r", base_ref="main", head_sha="abc123",
            user_pat="github_pat_fake",
            review_id="testrev001", finding_index=0,
        )
        mock_open_pr.assert_not_called()

    assert result.status == "rejected"
    assert result.pr_url is None


def test_pipeline_cannot_fix():
    """how88 返回 CANNOT_FIX → rejected，不进入 DeepSeek 步骤。"""
    finding = _make_finding()

    with (
        patch("app.services.ai_fix_service.call_how88_for_patch", return_value="CANNOT_FIX:\nArchitectural issue."),
        patch("app.services.ai_fix_service.call_deepseek_for_review") as mock_ds,
    ):
        result = run_fix_pipeline(
            finding=finding,
            diff_context="diff",
            owner="u", repo="r", base_ref="main", head_sha="abc123",
            user_pat="github_pat_fake",
            review_id="testrev001", finding_index=0,
        )
        mock_ds.assert_not_called()

    assert result.status == "rejected"


def test_pipeline_how88_error():
    """how88 网络失败 → 状态为 error。"""
    finding = _make_finding()

    with patch("app.services.ai_fix_service.call_how88_for_patch", side_effect=Exception("timeout")):
        result = run_fix_pipeline(
            finding=finding,
            diff_context="diff",
            owner="u", repo="r", base_ref="main", head_sha="abc123",
            user_pat="github_pat_fake",
            review_id="testrev001", finding_index=0,
        )

    assert result.status == "error"
    assert "timeout" in result.error


# ---------- API 层测试 ----------

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
def authed_client_with_pat(db_session):
    """已注册、登录、绑定了 mock PAT 的 TestClient。"""
    app = create_app()

    def _override():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_session] = _override
    with TestClient(app) as c:
        # 注册
        resp = c.post("/api/auth/register", json={"username": "fixer", "password": "pass1234"})
        token = resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # 绑定 mock PAT（mock 验证 GitHub）
        with patch("app.api.user._verify_github_pat", return_value="fixer-gh"):
            c.put("/api/user/github-pat", json={"pat": "github_pat_" + "x" * 30}, headers=headers)

        c.headers.update(headers)
        yield c

    app.dependency_overrides.clear()


def test_fix_endpoint_requires_auth(db_session):
    app = create_app()

    def _override():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_session] = _override
    with TestClient(app) as c:
        resp = c.post("/api/review/nonexistent/fix/0")
        assert resp.status_code == 401
    app.dependency_overrides.clear()


def test_fix_endpoint_review_not_found(authed_client_with_pat):
    resp = authed_client_with_pat.post("/api/review/nonexistent_id/fix/0")
    assert resp.status_code == 404


def test_fix_endpoint_review_not_done(authed_client_with_pat):
    """job 存在但尚未完成 → 409。"""
    from app.api.review import _Job, _jobs, ReviewRequest
    fake_job = _Job(ReviewRequest(url="https://github.com/u/r/pull/1"))
    fake_job.status = "running"
    _jobs["testjob001"] = {"job": fake_job}

    resp = authed_client_with_pat.post("/api/review/testjob001/fix/0")
    assert resp.status_code == 409

    del _jobs["testjob001"]
