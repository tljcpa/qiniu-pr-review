"""多模型交叉验证（亮点 4）。

对 reasoner 已 confirmed 且 severity=high 的 finding，用异构模型（Azure GPT-4.1-mini）
做独立第二意见，结果写进 RawFinding.cross_check（none/agree/disagree）+ cross_note。

设计见复盘 D-24 / D-25：
- 只验高风险 confirmed（Azure 配额有限，花在刀刃上）。
- agree -> 置信度加分；disagree -> 降权并标注分歧（由置信度公式 D-08 消费 cross_check）。
- 可禁用 / Azure 不可用时静默跳过（cross_check 保持 none），保证没有 Azure 也能完整跑。
"""

from __future__ import annotations

import json
import re

from app.services.llm_provider import LLMError, get_verifier_provider
from app.services.prompts import (
    CROSS_VALIDATE_SYSTEM,
    CROSS_VALIDATE_USER_TEMPLATE,
)
from app.services.router import RawFinding, RawReview


def _safe_json(text: str) -> dict | None:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\n", "", cleaned)
        cleaned = re.sub(r"\n```$", "", cleaned)
    try:
        return json.loads(cleaned)
    except (json.JSONDecodeError, TypeError):
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m is not None:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                return None
        return None


def _should_validate(f: RawFinding) -> bool:
    """只对 reasoner 确认的高风险 finding 做交叉验证（D-24）。"""
    return f.verdict == "confirmed" and f.severity == "high"


class CrossValidator:
    """对 RawReview 里的高风险 finding 做异构模型交叉验证。"""

    def __init__(self, *, verifier_provider=None, enabled: bool = True) -> None:
        self._verifier = verifier_provider
        self._enabled = enabled

    def _get_verifier(self):
        if self._verifier is None:
            self._verifier = get_verifier_provider()
        return self._verifier

    def validate(self, review: RawReview, context_text: str, emit=None) -> None:
        """就地给 review.findings 里的高风险项填 cross_check / cross_note。"""
        if emit is None:
            def emit(event_type, data):
                return None

        if not self._enabled:
            return

        targets = [f for f in review.findings if _should_validate(f)]
        if not targets:
            return

        try:
            verifier = self._get_verifier()
        except Exception as exc:
            # Azure 没配置 / 建 client 失败：静默跳过，不影响主流程
            review.trace.append(f"cross_validate: 跳过（验证模型不可用：{exc}）")
            return

        agree_n = 0
        disagree_n = 0
        for f in targets:
            emit("cross_validate_start", {"title": f.title, "file": f.file})
            messages = [
                {"role": "system", "content": CROSS_VALIDATE_SYSTEM},
                {
                    "role": "user",
                    "content": CROSS_VALIDATE_USER_TEMPLATE.format(
                        file=f.file,
                        line_hint=f.line_hint,
                        category=f.category,
                        title=f.title,
                        detail=f.detail,
                        context=context_text,
                    ),
                },
            ]
            try:
                resp = verifier.complete(messages, temperature=0.0)
            except LLMError as exc:
                # 单条验证失败：保持 none，不影响其他
                f.cross_check = "none"
                review.trace.append(f"cross_validate: {f.title} 验证失败 {exc}")
                continue

            data = _safe_json(resp.content)
            if data is None:
                f.cross_check = "none"
                review.trace.append(f"cross_validate: {f.title} 返回非 JSON，保持 none")
                continue

            agree = bool(data.get("agree", False))
            note = str(data.get("reason", ""))
            if agree:
                f.cross_check = "agree"
                agree_n += 1
            else:
                f.cross_check = "disagree"
                disagree_n += 1
            f.cross_note = note
            emit("cross_validate_done", {
                "title": f.title,
                "agree": agree,
                "model": resp.model,
            })

        review.trace.append(
            f"cross_validate: 验证 {len(targets)} 条高风险，agree {agree_n} / disagree {disagree_n}"
        )
