"""对外的结构化评审结果（Pydantic）。

接口冻结点（PR8 缓存、PR9 API、PR10 前端都按这里的 schema 来）。
职责：把路由层的 RawReview/RawFinding 转成带置信度、去重后的 Finding/ReviewReport，
并能直接 JSON 序列化给前端。

置信度评分见复盘 D-08，去重见 D-18。
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class Severity(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Confidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Category(str, Enum):
    BUG = "bug"
    SECURITY = "security"
    LOGIC = "logic"
    MAINTAINABILITY = "maintainability"
    STYLE = "style"


class Finding(BaseModel):
    """一条最终对外的评审发现。"""

    file: str
    line_hint: str = ""
    severity: Severity = Severity.MEDIUM
    category: Category = Category.BUG
    title: str
    detail: str = ""
    suggestion: str = ""
    confidence: Confidence = Confidence.MEDIUM
    # 置信度原始分（0~1），便于前端排序与调试
    confidence_score: float = 0.0
    # 核实结论：confirmed / uncertain / unverified
    verdict: str = "unverified"
    # 是否经 reasoner 深读
    deep_read: bool = False
    # AI 思维链原文（亮点 3：UI 可展开）。None 表示该条未深读
    reasoning: str | None = None
    # 交叉验证结论（PR12 填充）：agree / disagree / none
    cross_check: str = "none"


class ReviewReport(BaseModel):
    """一次 PR review 的完整对外结果。"""

    summary: str = ""
    context_level: str = ""
    findings: list[Finding] = Field(default_factory=list)
    # 统计便于前端展示与误报分析
    total_findings: int = 0
    high_count: int = 0
    medium_count: int = 0
    low_count: int = 0
    usage: dict = Field(default_factory=dict)
    trace: list[str] = Field(default_factory=list)


# ---- 置信度评分（D-08）----

def _reasoning_signal(reasoning: str | None) -> float:
    """思维链充分度 -> 0~0.2，越长越高但封顶（防靠啰嗦刷分）。"""
    if not reasoning:
        return 0.0
    length = len(reasoning)
    # 800 字以上视为充分推理，给满 0.2；线性映射
    ratio = min(length / 800.0, 1.0)
    return round(0.2 * ratio, 3)


def compute_confidence_score(
    verdict: str,
    deep_read: bool,
    reasoning: str | None,
    severity: str,
    cross_check: str = "none",
) -> float:
    """加权求和各信号得到 0~1 置信分（公式见复盘 D-08）。"""
    score = 0.0

    # 1) verdict 主导
    if verdict == "confirmed":
        score += 0.5
    elif verdict == "uncertain":
        score += 0.1
    # unverified / false_positive 不加（false_positive 本就不该到这）

    # 2) 是否深读
    if deep_read:
        score += 0.15

    # 3) 思维链充分度
    score += _reasoning_signal(reasoning)

    # 4) 交叉验证
    if cross_check == "agree":
        score += 0.15
    elif cross_check == "disagree":
        score -= 0.2

    # 5) 严重度自评（轻微加权，不主导）
    if severity == "high":
        score += 0.1
    elif severity == "medium":
        score += 0.05

    # 夹到 [0, 1]
    if score < 0.0:
        score = 0.0
    if score > 1.0:
        score = 1.0
    return round(score, 3)


def score_to_confidence(score: float) -> Confidence:
    """分数分档。"""
    if score >= 0.65:
        return Confidence.HIGH
    if score >= 0.35:
        return Confidence.MEDIUM
    return Confidence.LOW


def _normalize_severity(value: str) -> Severity:
    try:
        return Severity(value.lower())
    except ValueError:
        return Severity.MEDIUM


def _normalize_category(value: str) -> Category:
    try:
        return Category(value.lower())
    except ValueError:
        return Category.BUG


# ---- 去重（D-18）----

def _dedup_key(file: str, category: str, line_hint: str) -> tuple:
    """去重键：规范化文件名 + 类别 + 定位线索。

    用"位置"而非"标题"做去重信号：模型对同一问题的标题措辞会有出入，
    但同一文件、同一类别、同一定位基本就是同一个问题（D-18）。
    line_hint 去掉所有空白再小写，吸收 "L10" / "line 10" 之外的细微差异。
    """
    norm_file = file.strip().lower()
    norm_hint = "".join(line_hint.split()).lower()
    return (norm_file, category, norm_hint)
