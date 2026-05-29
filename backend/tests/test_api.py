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
    # 每个测试前后都重置限流器，避免前一个测试的请求计数干扰（限流器是模块级单例）
    review_module._rate_limiter.reset()
    yield
    review_module.set_service_factory(review_module._default_service_factory)
    review_module._jobs.clear()
    review_module._rate_limiter.reset()


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_create_review_returns_id(client):
    review_module.set_service_factory(lambda: _StubService())
    r = client.post("/api/review", json={"url": "https://github.com/o/r/pull/1"})
    assert r.status_code == 200
    assert "review_id" in r.json()
    assert len(r.json()["review_id"]) == 12


def test_create_review_rejects_invalid_url(client):
    # 非法 PR URL 直接 400，不创建任务（见复盘 D-32）
    for bad in ["", "   ", "not a url", "https://example.com/foo", "https://github.com/o/r"]:
        r = client.post("/api/review", json={"url": bad})
        assert r.status_code == 400, f"应拒绝: {bad!r}"
    # 合法简写与完整 URL 都应放行
    review_module.set_service_factory(lambda: _StubService())
    assert client.post("/api/review", json={"url": "o/r#7"}).status_code == 200
    assert client.post(
        "/api/review", json={"url": "https://github.com/o/r/pull/7"}
    ).status_code == 200


def test_job_multi_subscriber_each_gets_full_history():
    # 修复 D-29：多个连接订阅同一 review 时，各自拿到完整事件序列，互不抢事件。
    job = review_module._Job(review_module.ReviewRequest(url="http://pr"))
    sub_a = job.subscribe()
    # 发布两个事件 + 哨兵
    job.publish({"event": "fetch_done", "data": {}})
    job.publish({"event": "done", "data": {}})
    job.publish(review_module._DONE)

    # 第二个连接在任务结束后才接入，应能回放到全部历史（旧实现会拿不到任何事件）
    sub_b = job.subscribe()

    def drain(q):
        out = []
        while not q.empty():
            out.append(q.get_nowait())
        return out

    a = drain(sub_a)
    b = drain(sub_b)
    # A 收到两个事件 + 哨兵
    assert {"event": "fetch_done", "data": {}} in a
    assert review_module._DONE in a
    # B（迟到连接）同样拿到完整历史，不是空的
    assert any(x != review_module._DONE and x.get("event") == "fetch_done" for x in b)
    assert review_module._DONE in b


def test_full_flow_stream_and_result(client):
    review_module.set_service_factory(lambda: _StubService())
    rid = client.post("/api/review", json={"url": "https://github.com/o/r/pull/1"}).json()["review_id"]

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


def test_jobs_bounded_eviction(client, monkeypatch):
    # _jobs 是有界 LRU：超过上限丢最旧的，防长跑服务内存泄漏
    monkeypatch.setattr(review_module, "_MAX_JOBS", 3)
    review_module.set_service_factory(lambda: _StubService())
    ids = []
    for _ in range(5):
        rid = client.post("/api/review", json={"url": "https://github.com/o/r/pull/1"}).json()["review_id"]
        ids.append(rid)
    # 最多保留 3 个，最早的 2 个应被淘汰
    assert len(review_module._jobs) == 3
    assert ids[0] not in review_module._jobs
    assert ids[-1] in review_module._jobs


def test_rate_limit_429(client, monkeypatch):
    # 公开端点超过 IP 限流应返回 429（见复盘 D-27）
    review_module.set_service_factory(lambda: _StubService())
    # 把上限压到 2 次便于测试
    from app.core.ratelimit import RateLimiter
    monkeypatch.setattr(review_module, "_rate_limiter", RateLimiter(max_calls=2, window=60))
    assert client.post("/api/review", json={"url": "https://github.com/o/r/pull/1"}).status_code == 200
    assert client.post("/api/review", json={"url": "https://github.com/o/r/pull/1"}).status_code == 200
    # 第 3 次超限
    r = client.post("/api/review", json={"url": "https://github.com/o/r/pull/1"})
    assert r.status_code == 429
    assert "频繁" in r.json()["detail"]


def test_cors_credentials_disabled():
    # CORS 不应允许携带凭证（避免 *+credentials 反射 Origin 的非法配置）
    from app.main import create_app
    from starlette.middleware.cors import CORSMiddleware

    app = create_app()
    cors = [m for m in app.user_middleware if m.cls is CORSMiddleware]
    assert cors, "应挂载 CORS 中间件"
    assert cors[0].kwargs.get("allow_credentials") is False


def test_fetch_error_surfaces(client):
    review_module.set_service_factory(lambda: _StubService(raise_fetch_error=True))
    rid = client.post("/api/review", json={"url": "https://github.com/o/r/pull/2"}).json()["review_id"]
    with client.stream("GET", f"/api/review/{rid}/stream") as resp:
        body = "".join(resp.iter_text())
    assert "event: error" in body
    assert "PR 不存在" in body
    # GET 结果应 500
    r = client.get(f"/api/review/{rid}")
    assert r.status_code == 500
