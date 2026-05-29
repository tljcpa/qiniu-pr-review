"""分层上下文构建（项目技术亮点核心）。

把 PullRequestData 组装成喂给模型的上下文，按改动规模分 L1-L4，受 token 预算动态裁剪。

四层（详见复盘 D-05 / D-15）：
- L1（总是）：PR 标题/描述 + 改动文件清单 + 每文件增删行数。永远在预算最高优先级。
- L2（总改动行 < l2_max_lines，默认 800）：每个改动文件的整文件全文 + 其 diff。
- L3（l2 <= 行 <= l3_max_lines，默认 3000）：抽取式——文件 import 段 + diff hunk 周边窗口
  + 同文件其他函数/类签名。比 L2 省 token 又保留结构。
- L4（> l3_max_lines）：仅 diff（patch），并显式声明"跨文件引用未分析，本段为局部 review"。

核心原则：
1. patch（diff）是必含基底——没有 diff 无从评审；全文/签名是"锦上添花"，预算不足先砍它们。
2. 超预算时主动声明哪些被裁（truncated_notes），绝不静默截断后让模型基于残缺上下文乱判。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

from app.config import settings
from app.services.github_fetcher import ChangedFile, PullRequestData
from app.services.tokens import estimate_tokens


class ContextLevel(str, Enum):
    L1 = "L1"  # 仅元信息（极端超预算兜底）
    L2 = "L2"  # 全文
    L3 = "L3"  # 抽取式
    L4 = "L4"  # 仅 diff


@dataclass
class FileContext:
    """单个改动文件最终进入上下文的内容。"""

    filename: str
    status: str
    additions: int
    deletions: int
    # diff 文本（基底，几乎总是包含）
    patch: str | None
    # 富化内容：整文件全文(L2) 或 抽取片段(L3)；L4 为 None
    enrichment: str | None = None
    # 这个文件实际用了哪种富化（用于报告与调试）
    enrichment_kind: str = "none"  # none / full / extract
    token_estimate: int = 0


@dataclass
class ContextBundle:
    """构建结果：交给路由层/模型的完整上下文。"""

    level: ContextLevel
    pr: PullRequestData
    overview: str  # L1 概览文本
    files: list[FileContext] = field(default_factory=list)
    total_tokens: int = 0
    budget: int = 0
    # 被裁剪/降级的说明，会原样进 prompt，让模型知道"哪些没看全"
    truncated_notes: list[str] = field(default_factory=list)

    def to_prompt_text(self) -> str:
        """把 bundle 渲染成给模型的纯文本上下文。"""
        parts: list[str] = []
        parts.append(self.overview)
        if self.truncated_notes:
            parts.append("\n## 上下文完整性声明")
            for note in self.truncated_notes:
                parts.append(f"- {note}")
        parts.append("\n## 改动详情")
        for fc in self.files:
            parts.append(f"\n### 文件: {fc.filename} ({fc.status}, +{fc.additions} -{fc.deletions})")
            if fc.patch:
                parts.append("```diff")
                parts.append(fc.patch)
                parts.append("```")
            else:
                parts.append("(无 diff：二进制或超大文件，仅按文件名提示)")
            if fc.enrichment:
                if fc.enrichment_kind == "full":
                    parts.append("文件完整内容：")
                else:
                    parts.append("相关代码片段（import / 函数签名 / 改动周边）：")
                parts.append("```")
                parts.append(fc.enrichment)
                parts.append("```")
        return "\n".join(parts)


# 抽取层用的简单正则（语言无关的启发式，主要面向 Python/JS/类 C 语法）
_IMPORT_RE = re.compile(r"^\s*(import|from|#include|using|require|use)\b")
# 函数/类/方法定义的签名行
_SIGNATURE_RE = re.compile(
    r"^\s*(def |class |async def |func |function |public |private |protected |"
    r"static |export |const \w+\s*=\s*\(|[A-Za-z_][\w<>\[\], ]*\s+\w+\s*\([^;{]*\)\s*\{?)"
)


def _parse_hunk_line_ranges(patch: str) -> list[tuple[int, int]]:
    """从 unified diff 的 @@ 头解析出新文件侧的改动行区间。

    形如 @@ -12,7 +12,9 @@ 表示新文件从第 12 行起 9 行受影响。
    返回 [(start, end), ...]（1-based，闭区间）。
    """
    ranges: list[tuple[int, int]] = []
    header_re = re.compile(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
    for line in patch.splitlines():
        m = header_re.match(line)
        if m is None:
            continue
        start = int(m.group(1))
        count = int(m.group(2)) if m.group(2) is not None else 1
        if count == 0:
            count = 1
        ranges.append((start, start + count - 1))
    return ranges


def _extract_relevant(full_text: str, patch: str | None, window: int = 40) -> str:
    """L3 抽取式：从文件全文中抽 import 段 + 签名行 + 改动周边窗口。

    目的：在远小于全文的 token 下，给模型足够的结构上下文。
    """
    lines = full_text.splitlines()
    n = len(lines)
    keep = [False] * n

    # 1) import 段与签名行：全留（它们是"地图"）
    for i, line in enumerate(lines):
        if _IMPORT_RE.match(line) or _SIGNATURE_RE.match(line):
            keep[i] = True

    # 2) 改动 hunk 周边窗口：上下各 window 行
    if patch:
        for start, end in _parse_hunk_line_ranges(patch):
            lo = max(0, start - 1 - window)
            hi = min(n, end + window)
            for i in range(lo, hi):
                keep[i] = True

    # 3) 组装：保留段之间用 "..." 折叠标记，避免误导模型以为代码连续
    out: list[str] = []
    prev_kept = False
    for i in range(n):
        if keep[i]:
            # 行号前缀帮助模型定位
            out.append(f"{i + 1:>5}  {lines[i]}")
            prev_kept = True
        else:
            if prev_kept:
                out.append("      ...")
            prev_kept = False
    return "\n".join(out)


def _build_overview(pr: PullRequestData, level: ContextLevel) -> str:
    """L1 概览：任何层级都包含的元信息。"""
    lines = [
        "## PR 概览",
        f"- 仓库: {pr.repo_full_name}",
        f"- 标题: {pr.title}",
        f"- 作者: {pr.author}  分支: {pr.base_ref} <- {pr.head_ref}",
        f"- 规模: +{pr.additions} -{pr.deletions}，共 {pr.changed_files_count} 个文件，{pr.commits} 个提交",
        f"- 本次 review 上下文层级: {level.value}",
    ]
    body = (pr.body or "").strip()
    if body:
        # 描述可能很长，截到合理长度（概览不该吃太多预算）
        snippet = body if len(body) <= 1200 else body[:1200] + " …(描述截断)"
        lines.append(f"- PR 描述:\n{snippet}")
    lines.append("\n## 改动文件清单")
    for f in pr.files:
        mark = ""
        if f.is_binary_or_too_large:
            mark = " [二进制/超大，无 diff]"
        lines.append(f"- {f.filename} ({f.status}, +{f.additions} -{f.deletions}){mark}")
    if pr.files_truncated:
        lines.append(f"- (文件清单已截断：仅展示前 {len(pr.files)} 个文件)")
    return "\n".join(lines)


def _decide_level(pr: PullRequestData) -> ContextLevel:
    """按总改动行数决定层级。"""
    total = pr.total_changed_lines
    if total < settings.context_l2_max_lines:
        return ContextLevel.L2
    if total <= settings.context_l3_max_lines:
        return ContextLevel.L3
    return ContextLevel.L4


class ContextBuilder:
    """把 PullRequestData 构建成 ContextBundle。

    fetcher 可选：传入则在 L2/L3 拉文件全文做富化；不传则跳过富化（单测无需网络）。
    """

    def __init__(self, fetcher=None, *, budget: int | None = None) -> None:
        self._fetcher = fetcher
        self._budget = budget if budget is not None else settings.context_token_budget

    def build(self, pr: PullRequestData) -> ContextBundle:
        level = _decide_level(pr)
        overview = _build_overview(pr, level)

        bundle = ContextBundle(
            level=level,
            pr=pr,
            overview=overview,
            budget=self._budget,
        )

        # L1 概览先计入预算（最高优先级，不可裁）
        used = estimate_tokens(overview)

        if level == ContextLevel.L4:
            bundle.truncated_notes.append(
                "本 PR 改动较大，已降级为 L4：仅基于各文件 diff 做局部 review，"
                "未加载文件全文，也未分析跨文件引用关系。涉及调用方/被调方的判断请人工复核。"
            )

        # 第一遍：所有文件的 patch（基底）按"改动行数降序"加入，patch 永远优先
        ordered = sorted(pr.files, key=lambda f: f.changes, reverse=True)
        file_ctxs: dict[str, FileContext] = {}
        for f in ordered:
            fc = FileContext(
                filename=f.filename,
                status=f.status,
                additions=f.additions,
                deletions=f.deletions,
                patch=f.patch,
            )
            patch_tokens = estimate_tokens(f.patch or "")
            if used + patch_tokens > self._budget and file_ctxs:
                # 预算耗尽且已经放进至少一个文件：剩余文件只记名，不放 diff
                bundle.truncated_notes.append(
                    f"diff 预算不足，文件 {f.filename} 的具体改动未纳入本次 review。"
                )
                fc.patch = None
                fc.token_estimate = 0
                file_ctxs[f.filename] = fc
                continue
            used += patch_tokens
            fc.token_estimate = patch_tokens
            file_ctxs[f.filename] = fc

        # 第二遍：L2/L3 富化（全文 / 抽取），按同样优先级，加得下才加
        if level in (ContextLevel.L2, ContextLevel.L3) and self._fetcher is not None:
            for f in ordered:
                fc = file_ctxs[f.filename]
                if fc.patch is None:
                    # 这个文件连 diff 都没放进来，不富化
                    continue
                if f.is_binary_or_too_large:
                    continue
                full = self._fetcher.fetch_file_content(
                    pr.repo_full_name, f.filename, pr.head_sha
                )
                if not full:
                    bundle.truncated_notes.append(
                        f"文件 {f.filename} 全文拉取失败，本次仅基于其 diff 评审。"
                    )
                    continue

                if level == ContextLevel.L2:
                    enrichment = full
                    kind = "full"
                else:
                    enrichment = _extract_relevant(full, fc.patch)
                    kind = "extract"

                enrich_tokens = estimate_tokens(enrichment)
                if used + enrich_tokens > self._budget:
                    bundle.truncated_notes.append(
                        f"上下文预算不足，文件 {f.filename} 未加载{'全文' if kind == 'full' else '扩展片段'}，"
                        f"仅基于 diff 评审。"
                    )
                    continue
                used += enrich_tokens
                fc.enrichment = enrichment
                fc.enrichment_kind = kind
                fc.token_estimate += enrich_tokens

        # 保持原始文件顺序输出（按 PR.files 顺序而非改动行降序）
        bundle.files = [file_ctxs[f.filename] for f in pr.files if f.filename in file_ctxs]
        bundle.total_tokens = used
        return bundle
