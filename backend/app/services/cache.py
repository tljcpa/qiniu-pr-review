"""进程内缓存（亮点 5：增量 review + diff hash 缓存）。

设计（详见复盘 D-09）：
- 内容寻址：key 由内容算 sha1，内容变 key 就变，旧条目自然不命中，无需手动失效。
- 两级：
  - 文件级：sha1(filename + patch) -> 该文件的 RawFinding 列表。增量 review 的原子单位。
  - 报告级：sha1(head_sha + 所有文件 diff 哈希) -> 整份 ReviewReport。同 PR 未变则秒回。
- 线程安全：FastAPI 多线程下用一把锁保护 dict。
- 不持久化：进程内够演示；接口留 get/put 抽象，将来可换 SQLite 后端。
"""

from __future__ import annotations

import hashlib
import threading


def file_diff_hash(filename: str, patch: str | None) -> str:
    """单个文件改动的内容哈希（增量 review 的 key）。"""
    raw = f"{filename}\n{patch or ''}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def report_hash(head_sha: str, file_hashes: list[str]) -> str:
    """整份报告的 key：head_sha + 所有文件 diff 哈希（排序后）拼接。"""
    joined = head_sha + "|" + "|".join(sorted(file_hashes))
    return hashlib.sha1(joined.encode("utf-8")).hexdigest()


class InProcessCache:
    """线程安全的进程内 KV 缓存，带命中计数（答辩要的"缓存命中率"）。"""

    def __init__(self) -> None:
        self._store: dict[str, object] = {}
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0

    def get(self, key: str):
        with self._lock:
            if key in self._store:
                self._hits += 1
                return self._store[key]
            self._misses += 1
            return None

    def put(self, key: str, value: object) -> None:
        with self._lock:
            self._store[key] = value

    def has(self, key: str) -> bool:
        with self._lock:
            return key in self._store

    def stats(self) -> dict:
        with self._lock:
            total = self._hits + self._misses
            rate = 0.0
            if total > 0:
                rate = round(self._hits / total, 3)
            return {
                "hits": self._hits,
                "misses": self._misses,
                "size": len(self._store),
                "hit_rate": rate,
            }

    def clear(self) -> None:
        with self._lock:
            self._store.clear()
            self._hits = 0
            self._misses = 0


# 全局单例：整个进程共享一份缓存
review_cache = InProcessCache()
