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

from app.models.finding import ReviewReport
from app.services.cache import review_cache
from app.services.github_fetcher import GitHubFetchError
from app.services.review_service import ReviewService

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


class _Job:
    """单个 review 任务：持有请求参数、事件队列与最终结果。"""

    def __init__(self, req: "ReviewRequest") -> None:
        self.req = req
        self.queue: asyncio.Queue = asyncio.Queue()
        self.status = "pending"  # pending / running / done / error
        self.report: ReviewReport | None = None
        self.error: str | None = None
        self.meta: dict = {}
        self.started = False


async def _run_job(job: _Job, req: ReviewRequest) -> None:
    """后台跑 review，把 emit 事件投递到 job.queue。"""
    loop = asyncio.get_running_loop()
    job.status = "running"

    def emit(event_type: str, data: dict) -> None:
        # 该回调在 threadpool 线程里被调用，必须线程安全地投递回事件循环
        payload = {"event": event_type, "data": data}
        loop.call_soon_threadsafe(job.queue.put_nowait, payload)

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
        job.queue.put_nowait({"event": "done", "data": job.meta})
    except GitHubFetchError as exc:
        job.status = "error"
        job.error = str(exc)
        job.queue.put_nowait({"event": "error", "data": {"message": str(exc)}})
    except Exception as exc:  # noqa: BLE001 - 兜底，任何异常都要让 SSE 收尾
        job.status = "error"
        job.error = f"{type(exc).__name__}: {exc}"
        job.queue.put_nowait({"event": "error", "data": {"message": job.error}})
    finally:
        # 哨兵：通知 SSE 流结束
        job.queue.put_nowait(None)


@router.post("/review")
async def create_review(req: ReviewRequest) -> dict:
    """登记一个 review 任务，立即返回 review_id。

    实际执行在客户端打开 /stream 时启动——这样任务的生命周期与 SSE 连接绑定，
    事件循环全程存活，避免后台任务在 POST 响应结束后被挂起（尤其在某些 ASGI 运行环境下）。
    """
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
        # 首次打开流时启动后台 review（与 SSE 连接同生命周期）
        if not job.started:
            job.started = True
            asyncio.create_task(_run_job(job, job.req))
        while True:
            try:
                item = await asyncio.wait_for(job.queue.get(), timeout=30.0)
            except asyncio.TimeoutError:
                # 空闲超时：先看客户端是否断开，否则发心跳保连接不被代理掐断
                if await request.is_disconnected():
                    break
                yield ": keep-alive\n\n"
                continue
            if item is None:
                # 哨兵：结束
                break
            yield _sse(item["event"], item["data"])

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


@router.get("/cache/stats")
async def cache_stats() -> dict:
    """缓存命中率等统计（答辩演示用）。"""
    return review_cache.stats()


def _sse(event: str, data: dict) -> str:
    """格式化一条 SSE 消息。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
