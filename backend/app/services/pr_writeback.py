"""把审查结果写回 GitHub PR（创新亮点：从"出报告"到"在 GitHub 上行动"）。

用 GH_TOKEN 调 REST `POST /repos/:o/:r/pulls/:n/reviews`，event=COMMENT（非破坏、可逆）：
- 风险代码行 -> inline 行内批注（line-based: path/line/side=RIGHT）
- 整体 -> summary review body（带各 finding 概要 + 思维链要点）

设计与边界见复盘 D-36：
- 只写 owner==tljcpa 的仓库（安全护栏，合规）。
- line_hint 是 LLM 自由文本，解析行号并校验是否落在 diff hunk 内；不在 diff 的归入 summary，不丢。
- inline 触发 422 时降级为纯 summary review，保证至少留下整体评审。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import httpx

from app.config import settings
from app.models.finding import Finding, ReviewReport
from app.services.github_fetcher import PullRequestData, parse_pr_url

# 安全护栏：只允许把结果写回这些 owner 的仓库（小写比较）
_ALLOWED_OWNERS = {"tljcpa"}

_GITHUB_API = "https://api.github.com"

# unified diff 的 @@ 头：取新文件侧起始行与行数
_HUNK_RE = re.compile(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")

_SEV_EMOJI = {"high": "🔴", "medium": "🟠", "low": "🟡"}
_SEV_LABEL = {"high": "HIGH", "medium": "MEDIUM", "low": "LOW"}


class PRWritebackError(Exception):
    """写回 PR 失败。"""


@dataclass
class WritebackResult:
    ok: bool
    review_url: str = ""
    inline_count: int = 0
    summary_only_count: int = 0
    message: str = ""
    # 调试/展示用
    skipped: list = field(default_factory=list)


def _new_side_lines(patch: str | None) -> set[int]:
    """从 unified diff 解析"新文件侧"所有 diff 行号（可挂 inline 评论的行）。

    只统计 hunk 内的 context 行和新增行（+ 开头或空格开头），它们在新文件里有行号；
    删除行（- 开头）不在新文件，不能挂 RIGHT side 评论。
    """
    lines: set[int] = set()
    if not patch:
        return lines
    cur = None  # 当前新文件行号
    for raw in patch.splitlines():
        m = _HUNK_RE.match(raw)
        if m is not None:
            cur = int(m.group(1))
            continue
        if cur is None:
            continue
        if raw.startswith("-"):
            # 删除行：不占新文件行号
            continue
        if raw.startswith("\\"):
            # "\ No newline at end of file"
            continue
        # 新增行(+) 或 上下文行(空格开头)：占新文件一行
        lines.add(cur)
        cur += 1
    return lines


def _parse_line_number(line_hint: str) -> int | None:
    """从 line_hint 自由文本里抠出第一个整数当行号。"""
    if not line_hint:
        return None
    m = re.search(r"\d+", line_hint)
    if m is None:
        return None
    return int(m.group(0))


def _summary_body(report: ReviewReport, inline_n: int, summary_n: int) -> str:
    """组装整体 summary review 的 markdown body。"""
    parts: list[str] = []
    parts.append("## 🤖 AI PR Review 助手 · 自动评审")
    parts.append("")
    parts.append(report.summary or "（无总结）")
    parts.append("")
    parts.append(
        f"**统计**：共 {report.total_findings} 条发现"
        f"（高 {report.high_count} / 中 {report.medium_count} / 低 {report.low_count}）；"
        f"上下文层级 {report.context_level}；"
        f"已就 {inline_n} 条挂行内批注，{summary_n} 条列在下方。"
    )

    # 没能挂到 diff 行的 finding 在这里列出，避免丢信息
    summary_findings = [f for f in report.findings if getattr(f, "_inline", False) is False]
    if summary_findings:
        parts.append("")
        parts.append("### 未定位到 diff 行的发现")
        for f in summary_findings:
            parts.append(_finding_line(f))

    parts.append("")
    parts.append("---")
    parts.append(
        "*本评审由 AI 自动生成（event=COMMENT，不改变合并状态），仅供参考，请人工复核。*"
    )
    return "\n".join(parts)


def _finding_line(f: Finding) -> str:
    sev = _SEV_EMOJI.get(f.severity.value, "⚪")
    loc = f"`{f.file}{(':' + f.line_hint) if f.line_hint else ''}`"
    s = f"- {sev} **{_SEV_LABEL.get(f.severity.value, f.severity.value)}** {loc} — {f.title}"
    if f.suggestion:
        s += f"\n  - 建议：{f.suggestion}"
    return s


def _inline_comment_body(f: Finding) -> str:
    sev = _SEV_EMOJI.get(f.severity.value, "⚪")
    parts = [f"{sev} **{_SEV_LABEL.get(f.severity.value, f.severity.value)} · {f.category.value}** — {f.title}"]
    if f.detail:
        parts.append("")
        parts.append(f.detail)
    if f.suggestion:
        parts.append("")
        parts.append(f"**建议**：{f.suggestion}")
    # 思维链要点（截断，避免评论过长）
    if f.reasoning:
        snippet = f.reasoning.strip().replace("\n", " ")
        if len(snippet) > 240:
            snippet = snippet[:240] + "…"
        parts.append("")
        parts.append(f"<sub>🧠 推理要点：{snippet}</sub>")
    parts.append("")
    parts.append(f"<sub>置信度 {f.confidence.value} ({f.confidence_score:.2f})"
                 f"{' · 交叉验证一致' if f.cross_check == 'agree' else ''}</sub>")
    return "\n".join(parts)


class PRWritebackService:
    def __init__(self, token: str | None = None, *, client: httpx.Client | None = None) -> None:
        self._token = token if token is not None else settings.gh_token
        self._client = client  # 测试可注入

    def _headers(self) -> dict:
        return {
            "Authorization": f"token {self._token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _post_review(self, owner: str, repo: str, number: int, payload: dict) -> httpx.Response:
        url = f"{_GITHUB_API}/repos/{owner}/{repo}/pulls/{number}/reviews"
        if self._client is not None:
            return self._client.post(url, json=payload, headers=self._headers())
        with httpx.Client(timeout=30.0) as client:
            return client.post(url, json=payload, headers=self._headers())

    def write_back(self, pr_url: str, pr: PullRequestData, report: ReviewReport) -> WritebackResult:
        """把 report 写回 pr_url 指向的 PR。"""
        owner, repo, number = parse_pr_url(pr_url)

        # 安全护栏：只写允许的 owner
        if owner.lower() not in _ALLOWED_OWNERS:
            raise PRWritebackError(
                f"安全限制：只允许把评审写回 {sorted(_ALLOWED_OWNERS)} 的仓库，拒绝写 {owner}/{repo}。"
            )
        if not self._token:
            raise PRWritebackError("未配置 GH_TOKEN，无法写回 PR。")

        # 每个文件可挂 inline 的新文件侧行号集合
        diff_lines: dict[str, set[int]] = {
            f.filename: _new_side_lines(f.patch) for f in pr.files
        }

        comments = []
        for f in report.findings:
            line = _parse_line_number(f.line_hint)
            valid = (
                line is not None
                and f.file in diff_lines
                and line in diff_lines[f.file]
            )
            if valid:
                comments.append({
                    "path": f.file,
                    "line": line,
                    "side": "RIGHT",
                    "body": _inline_comment_body(f),
                })
                setattr(f, "_inline", True)
            else:
                setattr(f, "_inline", False)

        inline_n = len(comments)
        summary_n = sum(1 for f in report.findings if getattr(f, "_inline", False) is False)
        body = _summary_body(report, inline_n, summary_n)

        payload = {"event": "COMMENT", "body": body}
        if comments:
            payload["commit_id"] = pr.head_sha
            payload["comments"] = comments

        resp = self._post_review(owner, repo, number, payload)

        # inline 触发 422（行不在 diff/commit 不匹配等）-> 降级为纯 summary
        if resp.status_code == 422 and comments:
            fallback = {"event": "COMMENT", "body": body}
            resp = self._post_review(owner, repo, number, fallback)
            inline_n = 0
            summary_n = report.total_findings

        if resp.status_code not in (200, 201):
            raise PRWritebackError(
                f"GitHub 写回失败 HTTP {resp.status_code}: {resp.text[:200]}"
            )

        data = resp.json()
        return WritebackResult(
            ok=True,
            review_url=data.get("html_url", ""),
            inline_count=inline_n,
            summary_only_count=summary_n,
            message="已发布评审到 PR",
        )
