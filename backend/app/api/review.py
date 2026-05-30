"""Review API：POST 建任务 -> SSE 流式进度 -> GET 取最终结果。

设计见复盘 D-19：
- 同步的 ReviewService 在 run_in_threadpool 里跑，不阻塞事件循环。
- review 内部的 emit 回调（线程里调用）通过 call_soon_threadsafe 把事件投递到
  asyncio.Queue；SSE 端从队列取并按 text/event-stream 推给前端。
- 任务状态存进程内 dict（D-03：演示无需持久化）。
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections import OrderedDict

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.config import settings
from app.core.ratelimit import RateLimiter, client_ip
from app.models.finding import ReviewReport
from app.services.cache import review_cache
from app.services.github_fetcher import GitHubFetchError, parse_pr_url
from app.services.review_service import ReviewService

# 公开端点防刷闸：每 IP 每窗口最多 N 次（见复盘 D-27）
_rate_limiter = RateLimiter(settings.rate_limit_max, settings.rate_limit_window)

router = APIRouter(prefix="/api", tags=["review"])


class ReviewRequest(BaseModel):
    url: str
    use_cache: bool = True


# 任务态：review_id -> {job}。
# 用 OrderedDict 做有界 LRU：超过上限丢最旧的，避免长跑服务里 _jobs 无限增长（内存泄漏）。
_jobs: "OrderedDict[str, dict]" = OrderedDict()
# 进程内保留的最大任务数（演示足够；超出按插入顺序淘汰最旧）
_MAX_JOBS = 100


def _register_job(review_id: str, job: "_Job") -> None:
    """登记任务并执行有界淘汰。"""
    _jobs[review_id] = {"job": job}
    while len(_jobs) > _MAX_JOBS:
        # popitem(last=False)：丢最早插入的那个
        _jobs.popitem(last=False)


# service 工厂：默认建真 ReviewService；测试可替换为返回 stub 的工厂，避免打网络
def _default_service_factory() -> ReviewService:
    return ReviewService()


_service_factory = _default_service_factory


def set_service_factory(factory) -> None:
    """测试钩子：替换 review service 的构造方式。"""
    global _service_factory
    _service_factory = factory


# 哨兵：标记事件流结束（既进历史也广播，每个订阅者据此收尾）
_DONE = object()


class _Job:
    """单个 review 任务：历史事件缓冲 + 多订阅者广播（修复 SSE 多连接共享队列缺陷，见复盘 D-28/D-29）。

    旧实现所有连接共享一个 Queue，事件被某个连接取走后其他连接就拿不到，导致同一 review_id
    多连接时事件分流错乱、哨兵只被一个连接消费。改为：
    - history：按序记录全部事件（含 _DONE 哨兵），后到的连接先回放历史，不丢任何事件；
    - subscribers：每个活动连接一个独立 Queue，新事件广播给所有订阅者。
    所有队列操作都经事件循环线程（emit 走 call_soon_threadsafe），故无需额外锁。
    """

    def __init__(self, req: "ReviewRequest") -> None:
        self.req = req
        self.history: list = []  # 已发生的全部事件，按序
        self.subscribers: list[asyncio.Queue] = []  # 当前活动连接的队列
        self.done = False
        self.status = "pending"  # pending / running / done / error
        self.report: ReviewReport | None = None
        self.error: str | None = None
        self.meta: dict = {}
        self.started = False

    def publish(self, item) -> None:
        """记入历史并广播给所有订阅者（必须在事件循环线程调用）。"""
        self.history.append(item)
        if item is _DONE:
            self.done = True
        for q in self.subscribers:
            q.put_nowait(item)

    def subscribe(self) -> asyncio.Queue:
        """新连接订阅：原子地回放历史 + 注册队列（调用处到首个 await 之间不得让出）。"""
        q: asyncio.Queue = asyncio.Queue()
        # 先把已发生的历史按序塞进新队列（含可能已存在的 _DONE）
        for item in self.history:
            q.put_nowait(item)
        # 未结束才继续接收后续广播
        if not self.done:
            self.subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        if q in self.subscribers:
            self.subscribers.remove(q)


async def _run_job(job: _Job, req: ReviewRequest) -> None:
    """后台跑 review，把 emit 事件记入历史并广播给所有订阅连接。"""
    loop = asyncio.get_running_loop()
    job.status = "running"

    def emit(event_type: str, data: dict) -> None:
        # 该回调在 threadpool 线程里被调用，必须线程安全地投递回事件循环
        payload = {"event": event_type, "data": data}
        loop.call_soon_threadsafe(job.publish, payload)

    service = _service_factory()
    try:
        outcome = await run_in_threadpool(
            lambda: service.review_pr(req.url, use_cache=req.use_cache, emit=emit)
        )
        job.report = outcome.report
        job.meta = {
            "from_cache": outcome.from_cache,
            "cached_files": outcome.cached_files,
            "reviewed_files": outcome.reviewed_files,
            "cache_stats": service.cache_stats(),
        }
        job.status = "done"
        job.publish({"event": "done", "data": job.meta})
    except GitHubFetchError as exc:
        job.status = "error"
        job.error = str(exc)
        job.publish({"event": "error", "data": {"message": str(exc)}})
    except Exception as exc:  # noqa: BLE001 - 兜底，任何异常都要让 SSE 收尾
        job.status = "error"
        job.error = f"{type(exc).__name__}: {exc}"
        job.publish({"event": "error", "data": {"message": job.error}})
    finally:
        # 哨兵：通知所有订阅连接结束
        job.publish(_DONE)


@router.post("/review")
async def create_review(req: ReviewRequest, request: Request) -> dict:
    """登记一个 review 任务，立即返回 review_id。

    实际执行在客户端打开 /stream 时启动——这样任务的生命周期与 SSE 连接绑定，
    事件循环全程存活，避免后台任务在 POST 响应结束后被挂起（尤其在某些 ASGI 运行环境下）。

    公开端点：按 IP 限流，防止被刷爆烧光 LLM 余额（见复盘 D-27）。
    """
    # 先校验 URL 格式（复用 parse_pr_url 这套唯一真源）：格式错直接 400，
    # 不占用限流配额、不创建任务、不调模型（见复盘 D-32）。
    try:
        parse_pr_url(req.url)
    except GitHubFetchError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    ip = client_ip(request)
    if not _rate_limiter.allow(ip):
        raise HTTPException(
            status_code=429,
            detail=f"请求过于频繁，请稍后再试（每 {settings.rate_limit_window} 秒最多 {settings.rate_limit_max} 次）。",
        )
    review_id = uuid.uuid4().hex[:12]
    _register_job(review_id, _Job(req))
    return {"review_id": review_id}


@router.get("/review/{review_id}/stream")
async def stream_review(review_id: str, request: Request) -> StreamingResponse:
    """SSE 流：实时推送 review 进度事件。"""
    entry = _jobs.get(review_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="review_id 不存在")
    job: _Job = entry["job"]

    async def event_gen():
        # 先告诉前端连接已建立
        yield _sse("connected", {"review_id": review_id})
        # 本连接独立订阅：回放历史 + 接收后续广播（多连接互不抢事件，见复盘 D-29）
        queue = job.subscribe()
        # 首次打开流时启动后台 review（与 SSE 连接同生命周期）；
        # 注意先 subscribe 再启动，避免事件在订阅前就发出而丢失
        if not job.started:
            job.started = True
            asyncio.create_task(_run_job(job, job.req))
        try:
            while True:
                try:
                    # 心跳间隔 15s：兼容空闲超时更短的代理/网关（自审 finding [4]，见复盘 D-28）
                    item = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    # 空闲超时：先看客户端是否断开，否则发心跳保连接不被代理掐断
                    if await request.is_disconnected():
                        break
                    yield ": keep-alive\n\n"
                    continue
                if item is _DONE:
                    # 哨兵：结束
                    break
                yield _sse(item["event"], item["data"])
        finally:
            job.unsubscribe(queue)

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # 关掉 nginx 缓冲，保证实时
        },
    )


@router.get("/review/{review_id}")
async def get_review(review_id: str) -> dict:
    """取最终结果（done 后调）。"""
    entry = _jobs.get(review_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="review_id 不存在")
    job: _Job = entry["job"]
    if job.status == "error":
        raise HTTPException(status_code=500, detail=job.error)
    if job.status != "done" or job.report is None:
        return {"status": job.status}
    return {
        "status": "done",
        "meta": job.meta,
        "report": job.report.model_dump(),
    }


@router.post("/review/{review_id}/publish")
async def publish_review(review_id: str) -> dict:
    """把审查结果写回原 PR（inline 行内批注 + summary review）。创新亮点，见复盘 D-36。"""
    entry = _jobs.get(review_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="review_id 不存在")
    job: _Job = entry["job"]
    if job.status != "done" or job.report is None:
        raise HTTPException(status_code=409, detail="审查尚未完成，无法发布")

    from app.services.github_fetcher import GitHubFetcher
    from app.services.pr_writeback import PRWritebackError, PRWritebackService

    def _do() -> dict:
        # 重新拉一次 PR 拿最新 diff（含 head_sha 与各文件 patch）
        pr = GitHubFetcher().fetch(job.req.url)
        result = PRWritebackService().write_back(job.req.url, pr, job.report)
        return {
            "ok": result.ok,
            "review_url": result.review_url,
            "inline_count": result.inline_count,
            "summary_only_count": result.summary_only_count,
            "message": result.message,
        }

    try:
        return await run_in_threadpool(_do)
    except PRWritebackError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except GitHubFetchError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"发布失败：{type(exc).__name__}: {exc}") from exc


@router.get("/cache/stats")
async def cache_stats() -> dict:
    """缓存命中率等统计（答辩演示用）。"""
    return review_cache.stats()


def _sse(event: str, data: dict) -> str:
    """格式化一条 SSE 消息。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
