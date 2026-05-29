"""缓存与增量 review 测试（全 stub，无网络）。"""

import json

from app.models.finding import ReviewReport
from app.services.cache import (
    InProcessCache,
    file_diff_hash,
    report_hash,
)
from app.services.github_fetcher import ChangedFile, PullRequestData
from app.services.llm_provider import LLMResponse
from app.services.review_service import ReviewService


# ---- 哈希 ----

def test_file_hash_changes_with_patch():
    a = file_diff_hash("x.py", "@@ -1 +1 @@\n+a\n")
    b = file_diff_hash("x.py", "@@ -1 +1 @@\n+b\n")
    assert a != b


def test_file_hash_stable():
    a = file_diff_hash("x.py", "patch")
    b = file_diff_hash("x.py", "patch")
    assert a == b


def test_report_hash_order_independent():
    a = report_hash("sha", ["h1", "h2"])
    b = report_hash("sha", ["h2", "h1"])
    assert a == b


# ---- InProcessCache ----

def test_cache_hit_miss_stats():
    c = InProcessCache()
    assert c.get("k") is None  # miss
    c.put("k", 123)
    assert c.get("k") == 123  # hit
    stats = c.stats()
    assert stats["hits"] == 1
    assert stats["misses"] == 1
    assert stats["hit_rate"] == 0.5
    assert stats["size"] == 1


# ---- ReviewService 缓存/增量 ----

def _pr(files, head_sha="head1"):
    return PullRequestData(
        owner="a", repo="b", number=1, title="t", body="d", author="u", state="open",
        base_ref="main", head_ref="f", base_sha="bs", head_sha=head_sha,
        additions=sum(f.additions for f in files),
        deletions=sum(f.deletions for f in files),
        changed_files_count=len(files), commits=1,
        html_url="http://x", files=files,
    )


def _file(name, patch):
    return ChangedFile(filename=name, status="modified", additions=2, deletions=1,
                       changes=3, patch=patch)


class _StubFetcher:
    def __init__(self, pr):
        self._pr = pr

    def fetch(self, url):
        return self._pr

    def fetch_file_content(self, repo, path, ref):
        return None  # 不富化，简化测试


class _CountingRouter:
    """记录被调次数的假 router，返回固定 RawReview。"""

    def __init__(self, raw_factory):
        self._raw_factory = raw_factory
        self.calls = 0

    def review(self, bundle, emit=None):
        self.calls += 1
        return self._raw_factory()


def _raw_review():
    from app.services.router import RawFinding, RawReview
    return RawReview(
        summary="测试总结", level="L2",
        findings=[RawFinding(
            file="x.py", line_hint="L1", severity="high", category="bug",
            title="问题", detail="d", suggestion="s",
            verdict="confirmed", reasoning="r" * 400, deep_read=True,
        )],
        usage={"total_tokens": 100},
    )


def test_report_cache_second_call_is_instant():
    pr = _pr([_file("x.py", "@@ +1 @@\n+a\n")])
    cache = InProcessCache()
    router = _CountingRouter(_raw_review)
    svc = ReviewService(fetcher=_StubFetcher(pr), router=router, cache=cache, enable_cross_validate=False)

    out1 = svc.review_pr("http://pr")
    assert out1.from_cache is False
    assert router.calls == 1

    out2 = svc.review_pr("http://pr")
    assert out2.from_cache is True
    assert router.calls == 1  # 第二次没再调模型
    assert out2.report.summary == "测试总结"


def test_report_returns_independent_copy():
    # 缓存返回的是深拷贝，外部改动不污染缓存
    pr = _pr([_file("x.py", "@@ +1 @@\n+a\n")])
    svc = ReviewService(fetcher=_StubFetcher(pr), router=_CountingRouter(_raw_review),
                        cache=InProcessCache(), enable_cross_validate=False)
    out1 = svc.review_pr("http://pr")
    out1.report.summary = "被改了"
    out2 = svc.review_pr("http://pr")
    assert out2.report.summary == "测试总结"


def test_changed_diff_invalidates_report_cache():
    # patch 变化 -> 报告 key 变 -> 重新评审
    cache = InProcessCache()
    router = _CountingRouter(_raw_review)

    pr1 = _pr([_file("x.py", "@@ +1 @@\n+a\n")], head_sha="sha1")
    svc1 = ReviewService(fetcher=_StubFetcher(pr1), router=router, cache=cache, enable_cross_validate=False)
    svc1.review_pr("http://pr")

    pr2 = _pr([_file("x.py", "@@ +1 @@\n+CHANGED\n")], head_sha="sha2")
    svc2 = ReviewService(fetcher=_StubFetcher(pr2), router=router, cache=cache, enable_cross_validate=False)
    out = svc2.review_pr("http://pr")
    assert out.from_cache is False
    assert router.calls == 2  # 内容变了，重新调


def test_use_cache_false_bypasses():
    pr = _pr([_file("x.py", "@@ +1 @@\n+a\n")])
    cache = InProcessCache()
    router = _CountingRouter(_raw_review)
    svc = ReviewService(fetcher=_StubFetcher(pr), router=router, cache=cache, enable_cross_validate=False)
    svc.review_pr("http://pr", use_cache=True)
    svc.review_pr("http://pr", use_cache=False)
    assert router.calls == 2  # 第二次绕过缓存强制重跑
