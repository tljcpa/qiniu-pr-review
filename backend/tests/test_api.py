"""Review API 测试（FastAPI TestClient，进程内，不起真实服务、不打网络）。

用 stub service factory 替换真 ReviewService，stub 的 review_pr 会调用 emit 模拟进度事件，
从而验证：POST 建任务、SSE 推送事件、GET 取最终结果、错误处理。
"""

import json

import pytest
from fastapi.testclient import TestClient

from app.api import review as review_module
from app.main import create_app
from app.models.finding import Finding, ReviewReport
from app.services.github_fetcher import GitHubFetchError
from app.services.review_service import ReviewOutcome


class _StubService:
    """假 review service：emit 一串事件并返回固定 report。"""

    def __init__(self, *, raise_fetch_error=False):
        self._raise = raise_fetch_error

    def review_pr(self, url, *, use_cache=True, emit=None):
        if emit is None:
            emit = lambda e, d: None
        if self._raise:
            raise GitHubFetchError("PR 不存在")
        emit("fetch_start", {"url": url})
        emit("fetch_done", {"title": "测试 PR", "additions": 5, "deletions": 1, "changed_files": 1})
        emit("context_built", {"level": "L2", "tokens": 1000, "truncated": 0})
        emit("scan_done", {"summary": "总结", "candidate_count": 1})
        emit("finding_verdict", {"index": 0, "verdict": "confirmed", "title": "空指针", "dropped": False})
        report = ReviewReport(
            summary="测试总结", context_level="L2",
            findings=[Finding(file="x.py", title="空指针", severity="high",
                              category="bug", confidence="high", verdict="confirmed",
                              deep_read=True, reasoning="思维链")],
            total_findings=1, high_count=1,
        )
        return ReviewOutcome(report=report, from_cache=False, reviewed_files=1)

    def cache_stats(self):
        return {"hits": 0, "misses": 1, "size": 1, "hit_rate": 0.0}


@pytest.fixture
def client():
    app = create_app()
    return TestClient(app)


@pytest.fixture(autouse=True)
def _restore_factory():
    # 每个测试后恢复默认工厂，避免互相污染
    yield
    review_module.set_service_factory(review_module._default_service_factory)
    review_module._jobs.clear()


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_create_review_returns_id(client):
    review_module.set_service_factory(lambda: _StubService())
    r = client.post("/api/review", json={"url": "http://pr"})
    assert r.status_code == 200
    assert "review_id" in r.json()
    assert len(r.json()["review_id"]) == 12


def test_full_flow_stream_and_result(client):
    review_module.set_service_factory(lambda: _StubService())
    rid = client.post("/api/review", json={"url": "http://pr"}).json()["review_id"]

    # 消费 SSE 流（TestClient 同步读取整个流直到结束）
    with client.stream("GET", f"/api/review/{rid}/stream") as resp:
        assert resp.status_code == 200
        body = "".join(resp.iter_text())

    # 关键事件都应出现
    assert "event: connected" in body
    assert "event: fetch_done" in body
    assert "event: finding_verdict" in body
    assert "event: done" in body
    # 中文不被转义
    assert "空指针" in body

    # 取最终结果
    r = client.get(f"/api/review/{rid}")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "done"
    assert data["report"]["total_findings"] == 1
    assert data["report"]["findings"][0]["confidence"] == "high"
    assert data["report"]["findings"][0]["reasoning"] == "思维链"


def test_stream_unknown_id_404(client):
    r = client.get("/api/review/nonexistent/stream")
    assert r.status_code == 404


def test_get_unknown_id_404(client):
    r = client.get("/api/review/nonexistent")
    assert r.status_code == 404


def test_fetch_error_surfaces(client):
    review_module.set_service_factory(lambda: _StubService(raise_fetch_error=True))
    rid = client.post("/api/review", json={"url": "http://bad"}).json()["review_id"]
    with client.stream("GET", f"/api/review/{rid}/stream") as resp:
        body = "".join(resp.iter_text())
    assert "event: error" in body
    assert "PR 不存在" in body
    # GET 结果应 500
    r = client.get(f"/api/review/{rid}")
    assert r.status_code == 500
