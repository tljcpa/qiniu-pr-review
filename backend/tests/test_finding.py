"""置信度评分、分档、聚合、去重测试。"""

from app.models.finding import (
    Confidence,
    Severity,
    compute_confidence_score,
    score_to_confidence,
)
from app.services.aggregator import aggregate
from app.services.router import RawFinding, RawReview


# ---- 置信度评分 ----

def test_confirmed_with_long_reasoning_is_high():
    score = compute_confidence_score(
        verdict="confirmed", deep_read=True,
        reasoning="推" * 800, severity="high",
    )
    # 0.5 + 0.15 + 0.2 + 0.1 = 0.95
    assert score >= 0.65
    assert score_to_confidence(score) == Confidence.HIGH


def test_unverified_low_severity_is_low():
    score = compute_confidence_score(
        verdict="unverified", deep_read=False, reasoning=None, severity="low",
    )
    assert score < 0.35
    assert score_to_confidence(score) == Confidence.LOW


def test_uncertain_is_medium_ish():
    # uncertain(0.1) + deep_read(0.15) + 充分思维链(0.2) + medium(0.05) = 0.5 -> medium
    score = compute_confidence_score(
        verdict="uncertain", deep_read=True,
        reasoning="一些推理过程" * 200, severity="medium",
    )
    assert score_to_confidence(score) == Confidence.MEDIUM


def test_cross_disagree_penalizes():
    base = compute_confidence_score("confirmed", True, "推" * 800, "high", "none")
    dis = compute_confidence_score("confirmed", True, "推" * 800, "high", "disagree")
    assert dis < base


def test_reasoning_signal_capped():
    # 极长思维链不应超过 0.2 的贡献：对比只差 reasoning 的两次打分
    short = compute_confidence_score("uncertain", False, "x" * 800, "low")
    longer = compute_confidence_score("uncertain", False, "x" * 100000, "low")
    assert abs(longer - short) < 1e-9  # 都已封顶


def test_score_clamped():
    s = compute_confidence_score("confirmed", True, "推" * 5000, "high", "agree")
    assert 0.0 <= s <= 1.0


# ---- 聚合 ----

def _raw(title, file="x.py", verdict="confirmed", deep=True, reasoning="r" * 400,
         severity="high", category="bug", line_hint="L1"):
    return RawFinding(
        file=file, line_hint=line_hint, severity=severity, category=category,
        title=title, detail="d", suggestion="s",
        verdict=verdict, reasoning=reasoning, deep_read=deep,
    )


def test_aggregate_basic_stats():
    raw = RawReview(summary="总结", level="L2", findings=[
        _raw("高危问题", severity="high", line_hint="L10"),
        _raw("低危问题", verdict="unverified", deep=False, reasoning=None,
             severity="low", line_hint="L20"),
    ])
    report = aggregate(raw)
    assert report.summary == "总结"
    assert report.total_findings == 2
    assert report.high_count + report.medium_count + report.low_count == 2
    assert report.high_count >= 1


def test_aggregate_dedup_keeps_higher_confidence():
    # 同文件同类别同标题前缀 -> 去重，保留 confirmed(高分)那条
    raw = RawReview(summary="s", level="L2", findings=[
        _raw("空指针风险", verdict="unverified", deep=False, reasoning=None),
        _raw("空指针风险在这里", verdict="confirmed", deep=True, reasoning="r" * 800),
    ])
    report = aggregate(raw)
    assert report.total_findings == 1
    assert report.findings[0].verdict == "confirmed"


def test_aggregate_sort_high_first():
    raw = RawReview(summary="s", level="L2", findings=[
        _raw("低", severity="low", verdict="uncertain", reasoning="r" * 10),
        _raw("高", severity="high", verdict="confirmed", reasoning="r" * 800),
    ])
    report = aggregate(raw)
    # high severity 排前面
    assert report.findings[0].severity == Severity.HIGH


def test_finding_json_serializable():
    raw = RawReview(summary="s", level="L2", findings=[_raw("问题")])
    report = aggregate(raw)
    # Pydantic -> dict/json 不报错
    payload = report.model_dump()
    assert payload["findings"][0]["title"] == "问题"
    js = report.model_dump_json()
    assert "confidence" in js
