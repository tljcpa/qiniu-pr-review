"""LLM 路由层测试（canned-JSON stub provider，无网络）。

覆盖：
1. 快扫解析候选 + summary
2. 逐条深读：confirmed 保留、false_positive 丢弃、思维链捕获
3. JSON 外包 ```json``` 容错
4. 非 JSON 返回的降级
5. max_deep_read 上限：超出的候选保留但标未深读
6. usage 累加
"""

import json

import pytest

from app.services.context_builder import ContextBuilder
from app.services.github_fetcher import ChangedFile, PullRequestData
from app.services.llm_provider import LLMResponse
from app.services.router import ReviewRouter, _safe_json_loads


def _bundle():
    f = ChangedFile(filename="x.py", status="modified", additions=3, deletions=1,
                    changes=4, patch="@@ -1 +1,3 @@\n+code\n")
    pr = PullRequestData(
        owner="a", repo="b", number=1, title="t", body="d", author="u", state="open",
        base_ref="main", head_ref="f", base_sha="bs", head_sha="hs",
        additions=3, deletions=1, changed_files_count=1, commits=1,
        html_url="http://x", files=[f],
    )
    return ContextBuilder().build(pr)


class _ScriptedProvider:
    """按预设脚本依次返回 LLMResponse。"""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def complete(self, messages, **kwargs):
        self.calls += 1
        item = self._responses.pop(0)
        return item


def _resp(content, reasoning=None, usage=None):
    return LLMResponse(
        content=content,
        reasoning_content=reasoning,
        usage=usage or {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        provider="stub",
    )


def test_safe_json_loads_plain():
    assert _safe_json_loads('{"a": 1}') == {"a": 1}


def test_safe_json_loads_fenced():
    assert _safe_json_loads('```json\n{"a": 1}\n```') == {"a": 1}


def test_safe_json_loads_embedded():
    assert _safe_json_loads('废话 {"a": 1} 更多废话') == {"a": 1}


def test_safe_json_loads_garbage():
    assert _safe_json_loads("完全不是 json") is None


def test_scan_and_confirm():
    scan_json = json.dumps({
        "summary": "这个 PR 修了登录",
        "findings": [
            {"file": "x.py", "line_hint": "L10", "severity": "high",
             "category": "bug", "title": "空指针", "detail": "d", "suggestion": "s"},
        ],
    })
    deep_json = json.dumps({
        "verdict": "confirmed", "severity": "high",
        "title": "确认空指针", "detail": "确实会崩", "suggestion": "加判空",
    })
    chat = _ScriptedProvider([_resp(scan_json)])
    reasoner = _ScriptedProvider([_resp(deep_json, reasoning="我一步步想：当 x 为 None 时…")])

    router = ReviewRouter(chat_provider=chat, reasoner_provider=reasoner)
    review = router.review(_bundle())

    assert review.summary == "这个 PR 修了登录"
    assert len(review.findings) == 1
    f = review.findings[0]
    assert f.verdict == "confirmed"
    assert f.deep_read is True
    assert f.source == "reasoner"
    assert f.reasoning.startswith("我一步步想")  # 思维链被捕获
    assert f.title == "确认空指针"  # 被深读结论覆盖
    # usage 应累加两次调用
    assert review.usage["total_tokens"] == 30


def test_false_positive_dropped():
    scan_json = json.dumps({
        "summary": "s",
        "findings": [
            {"file": "x.py", "line_hint": "L1", "severity": "medium",
             "category": "bug", "title": "疑似", "detail": "d", "suggestion": "s"},
        ],
    })
    deep_json = json.dumps({"verdict": "false_positive", "severity": "low",
                            "title": "其实没问题", "detail": "上下文表明安全", "suggestion": ""})
    chat = _ScriptedProvider([_resp(scan_json)])
    reasoner = _ScriptedProvider([_resp(deep_json, reasoning="仔细看其实有判空")])

    review = ReviewRouter(chat_provider=chat, reasoner_provider=reasoner).review(_bundle())
    # 误报被丢弃
    assert len(review.findings) == 0
    assert any("误报" in t for t in review.trace)


def test_scan_non_json_degrades():
    chat = _ScriptedProvider([_resp("我觉得这个 PR 还行")])
    reasoner = _ScriptedProvider([])
    review = ReviewRouter(chat_provider=chat, reasoner_provider=reasoner).review(_bundle())
    assert review.findings == []
    assert "无法解析" in review.summary or "非 JSON" in " ".join(review.trace)


def test_max_deep_read_cap():
    findings = [
        {"file": "x.py", "line_hint": f"L{i}", "severity": "low",
         "category": "bug", "title": f"问题{i}", "detail": "d", "suggestion": "s"}
        for i in range(5)
    ]
    scan_json = json.dumps({"summary": "s", "findings": findings})
    # 只允许深读 2 条
    deep_confirmed = json.dumps({"verdict": "confirmed", "severity": "low",
                                 "title": "确认", "detail": "d", "suggestion": "s"})
    chat = _ScriptedProvider([_resp(scan_json)])
    reasoner = _ScriptedProvider([_resp(deep_confirmed, reasoning="r")] * 2)

    review = ReviewRouter(chat_provider=chat, reasoner_provider=reasoner,
                          max_deep_read=2).review(_bundle())
    # 2 条深读确认 + 3 条未深读保留 = 5 条
    assert len(review.findings) == 5
    deep_done = [f for f in review.findings if f.deep_read]
    not_deep = [f for f in review.findings if not f.deep_read]
    assert len(deep_done) == 2
    assert len(not_deep) == 3
    assert all(f.verdict == "unverified" for f in not_deep)
    # reasoner 只被调了 2 次
    assert reasoner.calls == 2
