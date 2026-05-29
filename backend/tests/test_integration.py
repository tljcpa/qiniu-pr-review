"""全链路集成测试（见复盘 D-31）。

现有单测都是单模块隔离测的。这里只 stub 最底层的两个边界——GitHub 拉取与 LLM 模型调用，
让中间的真实组件全部串起来跑：
  ContextBuilder -> ReviewRouter(真实两段式编排) -> CrossValidator(真实) -> aggregate(真实评分/去重) -> InProcessCache(真实)
验证装配正确：层级判定、思维链透传、误报丢弃、交叉验证降级/加分、缓存命中，端到端一致。
不打网络。
"""

import json

import pytest

from app.services.cache import InProcessCache
from app.services.cross_validator import CrossValidator
from app.services.github_fetcher import ChangedFile, PullRequestData
from app.services.llm_provider import LLMResponse
from app.services.review_service import ReviewService
from app.services.router import ReviewRouter


# ---- 最底层边界 stub ----

class _StubFetcher:
    """假 GitHub：返回固定 PR，全文拉取返回简单内容。"""

    def __init__(self, pr: PullRequestData, content: str | None = "def f():\n    return 1\n"):
        self._pr = pr
        self._content = content

    def fetch(self, url):
        return self._pr

    def fetch_file_content(self, repo, path, ref):
        return self._content


class _ScriptedProvider:
    """按调用次序返回预设 LLMResponse；chat 一次、reasoner/verifier 每条候选一次。"""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def complete(self, messages, **kwargs):
        self.calls += 1
        if not self._responses:
            # 兜底：超出预设次数时复用最后一个（便于 verifier 多条）
            return self._last
        item = self._responses.pop(0)
        self._last = item
        return item


def _resp(content, reasoning=None):
    return LLMResponse(
        content=content,
        reasoning_content=reasoning,
        usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        provider="stub",
        model="stub-model",
    )


def _pr(patch="@@ -1,2 +1,3 @@\n line\n+risky\n", add=5, dele=1):
    f = ChangedFile(
        filename="app/pay.py", status="modified",
        additions=add, deletions=dele, changes=add + dele, patch=patch,
    )
    return PullRequestData(
        owner="o", repo="r", number=1, title="加支付", body="改了支付逻辑",
        author="dev", state="open", base_ref="main", head_ref="feat",
        base_sha="b", head_sha="h",
        additions=add, deletions=dele, changed_files_count=1, commits=1,
        html_url="https://github.com/o/r/pull/1", files=[f],
    )


def _build_service(*, scan_findings, deep_verdicts, verifier_agrees=True,
                   enable_cross=True, cache=None):
    """组装一个除 fetcher/LLM 外全真实的 ReviewService。"""
    scan_json = json.dumps({"summary": "本 PR 改了支付逻辑", "findings": scan_findings})
    chat = _ScriptedProvider([_resp(scan_json)])
    reasoner = _ScriptedProvider([
        _resp(json.dumps(v), reasoning="一步步分析：" + "推理" * 60) for v in deep_verdicts
    ])
    verifier = _ScriptedProvider([
        _resp(json.dumps({"agree": verifier_agrees, "reason": "独立复核结论"}))
        for _ in range(5)
    ])
    router = ReviewRouter(chat_provider=chat, reasoner_provider=reasoner)
    cross = CrossValidator(verifier_provider=verifier, enabled=enable_cross)
    svc = ReviewService(
        fetcher=_StubFetcher(_pr()),
        router=router,
        cache=cache if cache is not None else InProcessCache(),
        cross_validator=cross,
    )
    return svc


def test_end_to_end_confirmed_high_with_cross_validate():
    # chat 扫出 2 候选：一个高危(将被确认)、一个(将被判误报丢弃)
    svc = _build_service(
        scan_findings=[
            {"file": "app/pay.py", "line_hint": "L2", "severity": "high",
             "category": "security", "title": "SQL 注入", "detail": "拼接", "suggestion": "参数化"},
            {"file": "app/pay.py", "line_hint": "L9", "severity": "medium",
             "category": "bug", "title": "疑似空指针", "detail": "x", "suggestion": "判空"},
        ],
        deep_verdicts=[
            {"verdict": "confirmed", "severity": "high", "title": "SQL 注入确认",
             "detail": "确实可注入", "suggestion": "用参数化查询"},
            {"verdict": "false_positive", "severity": "low", "title": "其实安全",
             "detail": "有判空", "suggestion": ""},
        ],
        verifier_agrees=True,
    )
    out = svc.review_pr("http://pr", use_cache=False)
    r = out.report

    # 误报被丢弃 -> 只剩 1 条
    assert r.total_findings == 1
    f = r.findings[0]
    assert f.title == "SQL 注入确认"
    assert f.verdict == "confirmed"
    # 思维链透传到对外结果
    assert f.reasoning and "一步步分析" in f.reasoning
    # 高危 + confirmed + 交叉验证 agree -> 高置信
    assert f.cross_check == "agree"
    assert f.confidence == "high"
    assert r.high_count == 1
    assert r.context_level == "L2"  # 小 PR


def test_end_to_end_cross_disagree_downgrades():
    # 高危被确认，但交叉验证不同意 -> 降级 medium 并记分歧
    svc = _build_service(
        scan_findings=[
            {"file": "app/pay.py", "line_hint": "L2", "severity": "high",
             "category": "security", "title": "越权", "detail": "d", "suggestion": "s"},
        ],
        deep_verdicts=[
            {"verdict": "confirmed", "severity": "high", "title": "越权确认",
             "detail": "d", "suggestion": "s"},
        ],
        verifier_agrees=False,
    )
    r = svc.review_pr("http://pr", use_cache=False).report
    assert r.total_findings == 1
    f = r.findings[0]
    assert f.cross_check == "disagree"
    # 分歧 -> 高危降为中危
    assert f.severity == "medium"


def test_end_to_end_cache_hit_second_call():
    # 同一 PR 第二次 review 命中报告缓存，不再调模型
    cache = InProcessCache()
    svc = _build_service(
        scan_findings=[
            {"file": "app/pay.py", "line_hint": "L2", "severity": "high",
             "category": "bug", "title": "问题", "detail": "d", "suggestion": "s"},
        ],
        deep_verdicts=[
            {"verdict": "confirmed", "severity": "high", "title": "确认",
             "detail": "d", "suggestion": "s"},
        ],
        cache=cache,
    )
    out1 = svc.review_pr("http://pr", use_cache=True)
    assert out1.from_cache is False
    # 第二次：新建 service 但共享同一 cache，应命中
    svc2 = _build_service(
        scan_findings=[{"file": "x", "line_hint": "L1", "severity": "low",
                        "category": "bug", "title": "不该被用到", "detail": "d", "suggestion": "s"}],
        deep_verdicts=[{"verdict": "confirmed", "severity": "low", "title": "x",
                        "detail": "d", "suggestion": "s"}],
        cache=cache,
    )
    out2 = svc2.review_pr("http://pr", use_cache=True)
    assert out2.from_cache is True
    # 命中缓存 -> 返回的是第一次的结果，不是第二次 stub 的
    assert out2.report.findings[0].title == "确认"


def test_end_to_end_cross_validate_disabled():
    # 关闭交叉验证：高危 confirmed 但 cross_check 保持 none
    svc = _build_service(
        scan_findings=[
            {"file": "app/pay.py", "line_hint": "L2", "severity": "high",
             "category": "bug", "title": "高危", "detail": "d", "suggestion": "s"},
        ],
        deep_verdicts=[
            {"verdict": "confirmed", "severity": "high", "title": "高危确认",
             "detail": "d", "suggestion": "s"},
        ],
        enable_cross=False,
    )
    r = svc.review_pr("http://pr", use_cache=False).report
    assert r.findings[0].cross_check == "none"
    assert r.findings[0].severity == "high"  # 未被降级
