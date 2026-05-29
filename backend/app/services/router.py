"""LLM 路由层：两段式编排（项目亮点 2）。

流程（详见复盘 D-06 / D-16）：
  第一遍 deepseek-chat：对完整分层上下文快扫，产出总结 + 候选问题清单（JSON）。
  第二遍 deepseek-reasoner：对每条候选单独深读核实，confirmed/false_positive/uncertain，
                            并保留每条的 reasoning_content 思维链（亮点 3 数据源）。
  假阳性被丢弃 -> 误报控制主力。

产出 RawReview（dataclass），由 PR7 转成对外的 Pydantic Finding/ReviewReport（接口冻结见 D-17）。

所有 provider 可注入，路由层用 canned-JSON stub 即可全单测，无需网络。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from app.config import settings
from app.services.context_builder import ContextBundle
from app.services.llm_provider import (
    LLMError,
    get_chat_provider,
    get_reasoner_provider,
)
from app.services.prompts import (
    DEEP_READ_SYSTEM,
    DEEP_READ_USER_TEMPLATE,
    SCAN_SYSTEM,
    SCAN_USER_TEMPLATE,
)

# 单次 review 最多深读多少条候选，防止候选爆炸导致海量 reasoner 调用（D-16）
DEFAULT_MAX_DEEP_READ = 12


@dataclass
class RawFinding:
    """模型产出的单条问题（未做置信度归一/去重，那是 PR7 的事）。"""

    file: str
    line_hint: str
    severity: str  # high / medium / low
    category: str  # bug / security / logic / maintainability / style
    title: str
    detail: str
    suggestion: str
    # 深读阶段填充
    verdict: str = "unverified"  # unverified / confirmed / false_positive / uncertain
    # reasoner 的思维链原文（亮点 3：UI 上可逐条展开）
    reasoning: str | None = None
    # 是否经过 reasoner 深读（超出 max_deep_read 的候选为 False）
    deep_read: bool = False
    # 来源轨迹，便于调试与答辩演示
    source: str = "chat"  # chat / reasoner


@dataclass
class RawReview:
    """一次 PR review 的原始结果。"""

    summary: str
    findings: list[RawFinding] = field(default_factory=list)
    level: str = ""  # 上下文层级 L1-L4
    # token 用量累计，答辩要的数字
    usage: dict = field(default_factory=dict)
    # 过程轨迹（哪步调了什么模型、候选多少、确认多少），UI/调试用
    trace: list[str] = field(default_factory=list)


def _strip_json(text: str) -> str:
    """模型有时会包一层 ```json ... ```，剥掉再解析。"""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        # 去掉首行 ``` 或 ```json 和尾部 ```
        cleaned = re.sub(r"^```[a-zA-Z]*\n", "", cleaned)
        cleaned = re.sub(r"\n```$", "", cleaned)
    return cleaned.strip()


def _safe_json_loads(text: str) -> dict | None:
    """容错解析模型输出的 JSON：失败返回 None 而非抛。"""
    try:
        return json.loads(_strip_json(text))
    except (json.JSONDecodeError, TypeError):
        # 再尝试从文本里抠出第一个 {...} 块
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match is not None:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                return None
        return None


def _accumulate_usage(target: dict, usage: dict) -> None:
    """把一次调用的 usage 累加进总账。"""
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        if key in usage:
            target[key] = target.get(key, 0) + usage[key]


class ReviewRouter:
    """编排两段式 review。"""

    def __init__(
        self,
        *,
        chat_provider=None,
        reasoner_provider=None,
        max_deep_read: int = DEFAULT_MAX_DEEP_READ,
    ) -> None:
        # provider 延迟创建：传入则用注入的（测试），否则用工厂建真后端
        self._chat = chat_provider
        self._reasoner = reasoner_provider
        self._max_deep_read = max_deep_read

    def _get_chat(self):
        if self._chat is None:
            self._chat = get_chat_provider()
        return self._chat

    def _get_reasoner(self):
        if self._reasoner is None:
            self._reasoner = get_reasoner_provider()
        return self._reasoner

    def review(self, bundle: ContextBundle) -> RawReview:
        """对一个上下文 bundle 跑完整两段式 review。"""
        context_text = bundle.to_prompt_text()
        review = RawReview(summary="", level=bundle.level.value)

        # ---- 第一遍：chat 快扫 ----
        candidates = self._scan(context_text, review)

        # ---- 第二遍：reasoner 逐条深读 ----
        self._deep_read(candidates, context_text, review)

        return review

    def _scan(self, context_text: str, review: RawReview) -> list[RawFinding]:
        chat = self._get_chat()
        messages = [
            {"role": "system", "content": SCAN_SYSTEM},
            {"role": "user", "content": SCAN_USER_TEMPLATE.format(context=context_text)},
        ]
        try:
            resp = chat.complete(messages, temperature=0.0)
        except LLMError as exc:
            review.summary = f"快扫阶段失败：{exc}"
            review.trace.append(f"scan: ERROR {exc}")
            return []

        _accumulate_usage(review.usage, resp.usage)
        data = _safe_json_loads(resp.content)
        if data is None:
            review.summary = "模型未返回可解析的结果。"
            review.trace.append("scan: 返回非 JSON，无法解析")
            return []

        review.summary = data.get("summary", "")
        candidates: list[RawFinding] = []
        for item in data.get("findings", []):
            if not isinstance(item, dict):
                continue
            candidates.append(
                RawFinding(
                    file=str(item.get("file", "")),
                    line_hint=str(item.get("line_hint", "")),
                    severity=str(item.get("severity", "medium")).lower(),
                    category=str(item.get("category", "bug")).lower(),
                    title=str(item.get("title", "")),
                    detail=str(item.get("detail", "")),
                    suggestion=str(item.get("suggestion", "")),
                    source="chat",
                )
            )
        review.trace.append(f"scan: chat 产出 {len(candidates)} 条候选")
        return candidates

    def _deep_read(
        self, candidates: list[RawFinding], context_text: str, review: RawReview
    ) -> None:
        reasoner = self._get_reasoner()
        confirmed_count = 0

        for index, cand in enumerate(candidates):
            if index >= self._max_deep_read:
                # 超出上限：保留 chat 结论但标记未深读
                cand.deep_read = False
                cand.verdict = "unverified"
                review.findings.append(cand)
                continue

            messages = [
                {"role": "system", "content": DEEP_READ_SYSTEM},
                {
                    "role": "user",
                    "content": DEEP_READ_USER_TEMPLATE.format(
                        file=cand.file,
                        line_hint=cand.line_hint,
                        category=cand.category,
                        severity=cand.severity,
                        title=cand.title,
                        detail=cand.detail,
                        context=context_text,
                    ),
                },
            ]
            try:
                resp = reasoner.complete(messages, temperature=0.0)
            except LLMError as exc:
                # 深读失败：保留候选，标记未深读，不让整次 review 崩
                cand.deep_read = False
                cand.detail += f"\n（深读失败，保留快扫结论：{exc}）"
                review.findings.append(cand)
                review.trace.append(f"deep_read[{index}]: ERROR {exc}")
                continue

            _accumulate_usage(review.usage, resp.usage)
            # 思维链：无论裁决如何都保留（亮点 3）
            cand.reasoning = resp.reasoning_content
            cand.deep_read = True
            cand.source = "reasoner"

            data = _safe_json_loads(resp.content)
            if data is None:
                # 解析失败：保留候选但标 uncertain
                cand.verdict = "uncertain"
                review.findings.append(cand)
                review.trace.append(f"deep_read[{index}]: 返回非 JSON，标 uncertain")
                continue

            verdict = str(data.get("verdict", "uncertain")).lower()
            cand.verdict = verdict
            # 用核实后的结论覆盖（严重度可能被修正）
            cand.severity = str(data.get("severity", cand.severity)).lower()
            if data.get("title"):
                cand.title = str(data["title"])
            if data.get("detail"):
                cand.detail = str(data["detail"])
            if data.get("suggestion"):
                cand.suggestion = str(data["suggestion"])

            if verdict == "false_positive":
                # 假阳性：丢弃，不进最终结果（误报控制）
                review.trace.append(f"deep_read[{index}]: 判为误报，已丢弃 - {cand.title}")
                continue

            confirmed_count += 1
            review.findings.append(cand)

        review.trace.append(
            f"deep_read: 深读 {min(len(candidates), self._max_deep_read)} 条，"
            f"确认/保留 {confirmed_count} 条，最终 {len(review.findings)} 条"
        )
