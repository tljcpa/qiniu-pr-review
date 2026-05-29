"""极简进程内 IP 限流（滑动窗口）。

目的（见复盘 D-27）：公开的 POST /api/review 无鉴权，DeepSeek 余额有限，
demo 当天可能被刷爆烧光。加一个够用的防刷闸，不引第三方依赖、不做分布式。

线程安全：FastAPI 多线程下用一把锁保护时间戳表。
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict


class RateLimiter:
    """每个 key（这里用客户端 IP）在 window 秒内最多 max_calls 次。"""

    def __init__(self, max_calls: int, window: int) -> None:
        self._max = max_calls
        self._window = window
        self._lock = threading.Lock()
        # key -> 该 key 最近若干次请求的时间戳列表
        self._hits: dict[str, list[float]] = defaultdict(list)

    def allow(self, key: str, *, now: float | None = None) -> bool:
        """返回是否放行；放行时记一次。超限返回 False。"""
        if now is None:
            now = time.monotonic()
        cutoff = now - self._window
        with self._lock:
            times = self._hits[key]
            # 丢弃窗口外的旧时间戳（原地过滤）
            kept = [t for t in times if t > cutoff]
            if len(kept) >= self._max:
                # 超限：保留过滤后的列表（不记本次），拒绝
                self._hits[key] = kept
                return False
            kept.append(now)
            self._hits[key] = kept
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


def client_ip(request) -> str:
    """取真实客户端 IP。

    经 Caddy 反代后 request.client.host 是 127.0.0.1，真实 IP 在 X-Forwarded-For
    首段（Caddy 默认会带）。取不到再退回 request.client.host。
    """
    xff = request.headers.get("x-forwarded-for")
    if xff:
        # 形如 "client, proxy1, proxy2"，第一个是最初的客户端
        first = xff.split(",")[0].strip()
        if first:
            return first
    client = getattr(request, "client", None)
    if client is not None and getattr(client, "host", None):
        return client.host
    return "unknown"
