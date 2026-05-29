"""多模型交叉验证测试（scripted verifier，无网络）。"""

import json

from app.services.cross_validator import CrossValidator, _should_validate
from app.services.llm_provider import LLMResponse
from app.services.router import RawFinding, RawReview


def _finding(verdict="confirmed", severity="high", title="高危空指针"):
    return RawFinding(
        file="x.py", line_hint="L10", severity=severity, category="bug",
        title=title, detail="d", suggestion="s",
        verdict=verdict, deep_read=True, reasoning="r" * 400,
    )


class _ScriptedVerifier:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0
        self.model = "gpt-4.1-mini"

    def complete(self, messages, **kwargs):
        self.calls += 1
        return self._responses.pop(0)


def _resp(agree, reason="理由"):
    return LLMResponse(
        content=json.dumps({"agree": agree, "reason": reason}),
        usage={"total_tokens": 20}, provider="azure", model="gpt-4.1-mini",
    )


# ---- 范围判定 ----

def test_should_validate_only_high_confirmed():
    assert _should_validate(_finding("confirmed", "high")) is True
    assert _should_validate(_finding("confirmed", "medium")) is False
    assert _should_validate(_finding("uncertain", "high")) is False
    assert _should_validate(_finding("unverified", "high")) is False


# ---- 验证行为 ----

def test_agree_sets_cross_check():
    review = RawReview(summary="s", findings=[_finding()])
    cv = CrossValidator(verifier_provider=_ScriptedVerifier([_resp(True, "确实空指针")]))
    cv.validate(review, "ctx")
    assert review.findings[0].cross_check == "agree"
    assert review.findings[0].cross_note == "确实空指针"


def test_disagree_sets_cross_check():
    review = RawReview(summary="s", findings=[_finding()])
    cv = CrossValidator(verifier_provider=_ScriptedVerifier([_resp(False, "有判空保护")]))
    cv.validate(review, "ctx")
    assert review.findings[0].cross_check == "disagree"


def test_only_high_confirmed_validated():
    # 一个高危confirmed + 一个中危confirmed + 一个高危uncertain；只验第一个
    review = RawReview(summary="s", findings=[
        _finding("confirmed", "high", "高危A"),
        _finding("confirmed", "medium", "中危B"),
        _finding("uncertain", "high", "存疑C"),
    ])
    verifier = _ScriptedVerifier([_resp(True)])
    cv = CrossValidator(verifier_provider=verifier)
    cv.validate(review, "ctx")
    assert verifier.calls == 1  # 只验了高危confirmed那条
    assert review.findings[0].cross_check == "agree"
    assert review.findings[1].cross_check == "none"
    assert review.findings[2].cross_check == "none"


def test_disabled_skips():
    review = RawReview(summary="s", findings=[_finding()])
    verifier = _ScriptedVerifier([_resp(True)])
    cv = CrossValidator(verifier_provider=verifier, enabled=False)
    cv.validate(review, "ctx")
    assert verifier.calls == 0
    assert review.findings[0].cross_check == "none"


def test_non_json_keeps_none():
    review = RawReview(summary="s", findings=[_finding()])
    bad = LLMResponse(content="我觉得成立", usage={}, provider="azure")
    cv = CrossValidator(verifier_provider=_ScriptedVerifier([bad]))
    cv.validate(review, "ctx")
    assert review.findings[0].cross_check == "none"


# ---- 与聚合器联动：disagree 降级 + 置信度 ----

def test_aggregate_disagree_downgrades_severity():
    from app.services.aggregator import aggregate
    from app.models.finding import Severity

    review = RawReview(summary="s", level="L2", findings=[_finding("confirmed", "high")])
    # 先交叉验证判 disagree
    CrossValidator(verifier_provider=_ScriptedVerifier([_resp(False, "其实安全")])).validate(
        review, "ctx"
    )
    report = aggregate(review)
    f = report.findings[0]
    # 高危被分歧降为中危，cross_check 透传，note 保留
    assert f.severity == Severity.MEDIUM
    assert f.cross_check == "disagree"
    assert f.cross_note == "其实安全"


def test_aggregate_agree_boosts_confidence():
    from app.services.aggregator import aggregate

    base = RawReview(summary="s", level="L2", findings=[_finding("confirmed", "high")])
    agree = RawReview(summary="s", level="L2", findings=[_finding("confirmed", "high")])
    CrossValidator(verifier_provider=_ScriptedVerifier([_resp(True)])).validate(agree, "ctx")

    base_report = aggregate(base)
    agree_report = aggregate(agree)
    # agree 应让置信分更高
    assert agree_report.findings[0].confidence_score > base_report.findings[0].confidence_score
