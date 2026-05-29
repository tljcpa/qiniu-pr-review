"""Finding 聚合器：把路由层 RawReview 转成对外 ReviewReport。

职责（见复盘 D-08 / D-18）：
1. 给每条 RawFinding 算置信度分并分档
2. 去重（同一问题多次报告，保留置信度更高的）
3. 统计 high/medium/low 数量
4. 排序：先按严重度，再按置信度（高的在前），让 UI 重要的靠上
"""

from __future__ import annotations

from app.models.finding import (
    Category,
    Confidence,
    Finding,
    ReviewReport,
    Severity,
    _dedup_key,
    _normalize_category,
    _normalize_severity,
    compute_confidence_score,
    score_to_confidence,
)
from app.services.router import RawFinding, RawReview

# 排序用：严重度权重
_SEVERITY_ORDER = {Severity.HIGH: 0, Severity.MEDIUM: 1, Severity.LOW: 2}


def _raw_to_finding(raw: RawFinding) -> Finding:
    """单条 RawFinding -> Finding（含评分）。"""
    score = compute_confidence_score(
        verdict=raw.verdict,
        deep_read=raw.deep_read,
        reasoning=raw.reasoning,
        severity=raw.severity,
        cross_check=raw.cross_check,
    )
    # 交叉验证有分歧时：高风险不再当 high 主导，降一档以提醒人工复核（D-24）
    severity = _normalize_severity(raw.severity)
    if raw.cross_check == "disagree" and severity == Severity.HIGH:
        severity = Severity.MEDIUM
    return Finding(
        file=raw.file,
        line_hint=raw.line_hint,
        severity=severity,
        category=_normalize_category(raw.category),
        title=raw.title,
        detail=raw.detail,
        suggestion=raw.suggestion,
        confidence=score_to_confidence(score),
        confidence_score=score,
        verdict=raw.verdict,
        deep_read=raw.deep_read,
        reasoning=raw.reasoning,
        cross_check=raw.cross_check,
        cross_note=raw.cross_note,
    )


def _dedup(findings: list[Finding]) -> list[Finding]:
    """去重：相同 key 保留置信度分更高的一条。"""
    best: dict[tuple, Finding] = {}
    for f in findings:
        key = _dedup_key(f.file, f.category.value, f.line_hint)
        existing = best.get(key)
        if existing is None or f.confidence_score > existing.confidence_score:
            best[key] = f
    return list(best.values())


def _sort_findings(findings: list[Finding]) -> list[Finding]:
    """先严重度（high 在前），同severity 再按置信分降序。"""
    return sorted(
        findings,
        key=lambda f: (_SEVERITY_ORDER.get(f.severity, 1), -f.confidence_score),
    )


def aggregate(raw: RawReview) -> ReviewReport:
    """RawReview -> ReviewReport。"""
    findings = [_raw_to_finding(r) for r in raw.findings]
    findings = _dedup(findings)
    findings = _sort_findings(findings)

    high = sum(1 for f in findings if f.confidence == Confidence.HIGH)
    medium = sum(1 for f in findings if f.confidence == Confidence.MEDIUM)
    low = sum(1 for f in findings if f.confidence == Confidence.LOW)

    return ReviewReport(
        summary=raw.summary,
        context_level=raw.level,
        findings=findings,
        total_findings=len(findings),
        high_count=high,
        medium_count=medium,
        low_count=low,
        usage=raw.usage,
        trace=raw.trace,
    )
