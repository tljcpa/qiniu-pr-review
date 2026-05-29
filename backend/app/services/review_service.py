"""Review 编排服务：把 fetcher / context / router / cache / aggregator 串成一个入口。

对外只暴露 review_pr(url)，PR9 的 API 直接调它。

缓存与增量逻辑（亮点 5，见复盘 D-09）：
1. 拉 PR，算每个文件的 diff 哈希，再算整报告哈希。
2. 报告哈希命中 -> 直接返回缓存的 ReviewReport（同 PR 未 push 新内容，秒回）。
3. 否则构建上下文跑 router；逐文件检查 diff 哈希：命中的文件复用其 findings，
   只对新增/变化的文件交给模型（增量）。
4. 聚合成 ReviewReport，回填两级缓存。

说明：router 当前对整个 bundle 一次性 review（chat 扫全量）。真正的"逐文件增量"在
RawFinding 上按 file 归位实现——命中文件的 findings 从缓存取，未命中的从本次 router 结果取。
这样既保留 chat 看全局上下文的能力，又能跳过已 review 文件的 reasoner 深读结论。
"""

from __future__ import annotations

import copy
from dataclasses import dataclass

from app.models.finding import ReviewReport
from app.services.aggregator import aggregate
from app.services.cache import (
    InProcessCache,
    file_diff_hash,
    report_hash,
    review_cache,
)
from app.services.context_builder import ContextBuilder
from app.services.github_fetcher import GitHubFetcher
from app.services.router import RawReview, ReviewRouter


@dataclass
class ReviewOutcome:
    """一次 review 的结果 + 元信息（是否命中缓存、增量统计）。"""

    report: ReviewReport
    from_cache: bool = False
    cached_files: int = 0
    reviewed_files: int = 0


class ReviewService:
    def __init__(
        self,
        *,
        fetcher: GitHubFetcher | None = None,
        router: ReviewRouter | None = None,
        cache: InProcessCache | None = None,
        budget: int | None = None,
    ) -> None:
        # 全部可注入，单测用 stub 不打网络
        self._fetcher = fetcher if fetcher is not None else GitHubFetcher()
        self._router = router if router is not None else ReviewRouter()
        self._cache = cache if cache is not None else review_cache
        self._budget = budget

    def review_pr(self, url: str, *, use_cache: bool = True) -> ReviewOutcome:
        pr = self._fetcher.fetch(url)

        # 每文件 diff 哈希
        per_file_hash = {f.filename: file_diff_hash(f.filename, f.patch) for f in pr.files}
        full_key = report_hash(pr.head_sha, list(per_file_hash.values()))

        # 报告级命中：整份秒回
        if use_cache:
            cached_report = self._cache.get(full_key)
            if cached_report is not None:
                return ReviewOutcome(
                    report=copy.deepcopy(cached_report),
                    from_cache=True,
                    cached_files=len(pr.files),
                    reviewed_files=0,
                )

        # 构建上下文 + 跑 router
        builder = ContextBuilder(self._fetcher, budget=self._budget)
        bundle = builder.build(pr)
        raw = self._router.review(bundle)

        # 增量：把本次 findings 按文件归位，并用文件级缓存补齐/复用
        cached_files, reviewed_files = self._apply_file_cache(
            raw, per_file_hash, use_cache
        )

        report = aggregate(raw)

        # 回填两级缓存：存深拷贝，避免调用方拿到的 report 被后续改动污染缓存
        if use_cache:
            self._cache.put(full_key, copy.deepcopy(report))

        return ReviewOutcome(
            report=report,
            from_cache=False,
            cached_files=cached_files,
            reviewed_files=reviewed_files,
        )

    def _apply_file_cache(
        self, raw: RawReview, per_file_hash: dict, use_cache: bool
    ) -> tuple[int, int]:
        """文件级缓存：命中文件复用 findings，未命中的存入；返回 (复用文件数, 新评审文件数)。

        本次 router 已对全量跑过，这里做两件事：
        - 把本次产出的 findings 按文件存入文件级缓存（供该文件下次复用）
        - 对本次没产出 finding、但曾缓存过的文件，补回历史 findings（增量场景）
        """
        if not use_cache:
            return 0, len({f.file for f in raw.findings})

        # 本次结果按文件分组
        by_file: dict[str, list] = {}
        for finding in raw.findings:
            by_file.setdefault(finding.file, []).append(finding)

        cached_files = 0
        reviewed_files = 0

        for filename, fhash in per_file_hash.items():
            cache_key = f"file:{fhash}"
            if filename in by_file:
                # 本次评审了这个文件：存入缓存
                self._cache.put(cache_key, by_file[filename])
                reviewed_files += 1
            else:
                # 本次没产出该文件 finding：看缓存里有没有历史结论
                hit = self._cache.get(cache_key)
                if hit:
                    raw.findings.extend(copy.deepcopy(hit))
                    cached_files += 1

        return cached_files, reviewed_files

    def cache_stats(self) -> dict:
        return self._cache.stats()
