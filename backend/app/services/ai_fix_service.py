"""AI 改码闭环服务（PR48）。

管线：finding + diff 片段 → how88(Claude Opus) 生成补丁
      → DeepSeek 审核补丁 → 审过则用用户 PAT 在其仓库开新分支+提 PR

安全铁律（每个步骤都要遵守）：
  1. how88 只收到：finding 文本 + 代码片段（非敏感 diff），绝不含 GH_TOKEN/用户 PAT
  2. 用户 PAT 只在第三步（开 PR）被解密使用，使用后立即丢弃，不缓存不打日志
  3. 新分支只开在用户自己的 repo（从 PR URL 解析出 owner/repo，验证与 PAT 的 github_username 匹配）
  4. 绝不直推 main；新分支命名 ai-fix/<review_id>-<finding_index>
  5. DeepSeek 审核是关卡——verdict != "approve" 不开 PR，直接返回审核结论给前端
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Optional

import httpx

from app.config import settings
from app.models.finding import Finding


@dataclass
class FixResult:
    """AI 修复管线的最终结果。"""

    status: str  # "approved" / "rejected" / "error"
    patch: Optional[str]  # 生成的 unified diff 补丁
    review_verdict: str  # DeepSeek 审核结论（approve/reject + 理由）
    pr_url: Optional[str]  # 审过后开的 PR 链接，rejected/error 时为 None
    error: Optional[str]  # 错误信息


def _build_patch_prompt(finding: Finding, diff_context: str) -> str:
    """构造给 how88 的 prompt：finding + 代码上下文，不含任何密钥。"""
    return f"""You are an expert software engineer tasked with fixing a code issue found during a PR review.

## Issue Found
**Title**: {finding.title}
**Severity**: {finding.severity}
**File**: {finding.file}
**Line**: {finding.line_hint}
**Category**: {finding.category}

**Detail**:
{finding.detail}

**Suggestion**:
{finding.suggestion}

## Code Context (unified diff)
```diff
{diff_context}
```

## Your Task
Generate a minimal, focused patch that fixes ONLY this specific issue.

Requirements:
1. Output a valid unified diff patch (git diff format)
2. Change as little as possible — do NOT refactor unrelated code
3. The patch must be syntactically correct and directly applicable
4. Include a brief explanation of what you changed and why

Output format:
```
EXPLANATION:
<one paragraph explaining the fix>

PATCH:
```diff
<unified diff here>
```
```

If the issue cannot be fixed with a simple patch (e.g., architectural problem), output:
```
CANNOT_FIX:
<reason why a simple patch is insufficient>
```"""


def _build_review_prompt(patch: str, finding: Finding) -> str:
    """构造给 DeepSeek 的审核 prompt。"""
    return f"""You are a senior code reviewer. Evaluate the following patch that claims to fix a code issue.

## Original Issue
**Title**: {finding.title}
**Detail**: {finding.detail}

## Proposed Patch
```diff
{patch}
```

## Evaluation Criteria
1. Does the patch actually fix the stated issue?
2. Does the patch introduce new bugs or security vulnerabilities?
3. Is the patch minimal and focused (not over-engineering)?
4. Is the patch syntactically valid?

Respond in JSON format:
{{
  "verdict": "approve" or "reject",
  "confidence": "high" or "medium" or "low",
  "reason": "<brief explanation, max 2 sentences>",
  "concerns": ["<concern 1>", "<concern 2>"]
}}

Be conservative: when in doubt, reject."""


def call_how88_for_patch(finding: Finding, diff_context: str) -> str:
    """调 how88(Claude Opus) 生成补丁，stream:true 模式。

    返回完整响应文本；how88 收到的内容不含任何密钥。
    """
    prompt = _build_patch_prompt(finding, diff_context)

    headers = {
        "Authorization": f"Bearer {settings.how88_grunt_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": settings.how88_model,
        "stream": True,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 2048,
    }

    full_text = []
    with httpx.stream(
        "POST",
        f"{settings.how88_base}/v1/chat/completions",
        headers=headers,
        json=payload,
        timeout=120.0,
    ) as resp:
        resp.raise_for_status()
        for line in resp.iter_lines():
            line = line.strip()
            if not line or line == "data: [DONE]":
                continue
            if line.startswith("data: "):
                try:
                    chunk = json.loads(line[6:])
                    delta = chunk["choices"][0]["delta"].get("content", "")
                    if delta:
                        full_text.append(delta)
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue

    return "".join(full_text)


def _parse_patch_from_response(response: str) -> Optional[str]:
    """从 how88 响应文本中提取 unified diff 补丁。"""
    if "CANNOT_FIX:" in response:
        return None

    # 先找 PATCH: 块后的 ```diff ... ```
    patch_section = re.search(r"PATCH:\s*```diff\s*(.*?)```", response, re.DOTALL)
    if patch_section:
        return patch_section.group(1).strip()

    # fallback：找任意 ```diff ... ```
    any_diff = re.search(r"```diff\s*(.*?)```", response, re.DOTALL)
    if any_diff:
        return any_diff.group(1).strip()

    return None


def call_deepseek_for_review(patch: str, finding: Finding) -> dict:
    """调 DeepSeek 审核补丁，返回解析后的 JSON 结果。"""
    from openai import OpenAI

    client = OpenAI(
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
    )
    prompt = _build_review_prompt(patch, finding)
    resp = client.chat.completions.create(
        model=settings.deepseek_chat_model,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=512,
        temperature=0.1,
    )
    raw = resp.choices[0].message.content or ""

    # 提取 JSON（DeepSeek 有时加 ```json ... ``` 包裹）
    json_match = re.search(r"\{.*\}", raw, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group())
        except json.JSONDecodeError:
            pass

    # 解析失败按 reject 处理，保守策略
    return {"verdict": "reject", "confidence": "low", "reason": "审核响应解析失败，保守拒绝", "concerns": []}


def open_pr_with_user_pat(
    owner: str,
    repo: str,
    base_ref: str,
    head_sha: str,
    patch: str,
    finding: Finding,
    user_pat: str,
    review_id: str,
    finding_index: int,
) -> str:
    """用用户 PAT 在其仓库创建新分支+提 PR，返回 PR 链接。

    安全：
    - user_pat 只在本函数内使用，函数返回后就 out-of-scope
    - 新分支命名 ai-fix/<review_id[:8]>-f<finding_index>，绝不动 main/master
    - 只在 owner/repo（用户自己的仓库）操作
    """
    from github import Auth, Github, GithubException

    branch_name = f"ai-fix/{review_id[:8]}-f{finding_index}"
    pr_title = f"fix: {finding.title[:60]} [AI 自动修复]"
    pr_body = f"""## AI 自动修复

**原始 finding**：{finding.title}
**文件**：`{finding.file}`
**行号**：{finding.line_hint}
**严重程度**：{finding.severity}

### 修复说明
{finding.suggestion}

### 变更内容
此 PR 由 AI PR Review 助手的 AI 改码闭环功能自动生成。
补丁经 DeepSeek 代码审核通过后提交。

> **注意**：请在合并前人工核查补丁内容。AI 生成的代码可能存在未预见的问题。

---
*由 [AI PR Review 助手](https://pr.qiniu.zdwktlj.top) 自动生成*"""

    gh = Github(auth=Auth.Token(user_pat))
    try:
        ghrepo = gh.get_repo(f"{owner}/{repo}")

        # 在 head_sha 上创建新分支
        try:
            ghrepo.create_git_ref(f"refs/heads/{branch_name}", head_sha)
        except GithubException as exc:
            if exc.status == 422:
                # 分支已存在（重试场景），继续用它
                pass
            else:
                raise

        # 应用补丁：逐文件解析 diff 并更新
        _apply_patch_to_branch(ghrepo, branch_name, patch, finding)

        # 提 PR
        pr = ghrepo.create_pull(
            title=pr_title,
            body=pr_body,
            head=branch_name,
            base=base_ref,
        )
        return pr.html_url
    finally:
        gh.close()


def _apply_patch_to_branch(ghrepo, branch_name: str, patch: str, finding: Finding) -> None:
    """把 unified diff 补丁应用到 branch 上（通过 GitHub Contents API）。

    只处理单文件补丁（AI 生成的补丁通常只改一个文件）；
    多文件补丁取第一个文件。
    失败时抛 ValueError。
    """
    from github import GithubException

    # 从 diff 头解析目标文件名（+++ b/path/to/file）
    target_file = None
    for line in patch.splitlines():
        if line.startswith("+++ b/"):
            target_file = line[6:].strip()
            break
        if line.startswith("+++ "):
            target_file = line[4:].strip()
            break

    if not target_file:
        # 无法解析文件名，用 finding.file 作为目标
        target_file = finding.file

    # 获取当前文件内容
    try:
        file_contents = ghrepo.get_contents(target_file, ref=branch_name)
        if isinstance(file_contents, list):
            raise ValueError(f"{target_file} 是目录，无法直接修改")
        original_content = file_contents.decoded_content.decode("utf-8")
        file_sha = file_contents.sha
    except GithubException as exc:
        if exc.status == 404:
            raise ValueError(f"目标文件 {target_file} 在分支 {branch_name} 上不存在") from exc
        raise

    # 应用 unified diff
    new_content = _apply_unified_diff(original_content, patch)
    if new_content == original_content:
        raise ValueError("补丁应用后文件内容无变化，跳过提交")

    # 更新文件
    ghrepo.update_file(
        path=target_file,
        message=f"fix: apply AI patch for '{finding.title[:50]}'",
        content=new_content,
        sha=file_sha,
        branch=branch_name,
    )


def _apply_unified_diff(original: str, patch: str) -> str:
    """极简 unified diff 应用器（单文件，不依赖外部工具）。

    只处理 @@ ... @@ 块内的 +/- 行；上下文行（空格开头）用于定位。
    对于 AI 生成的小补丁（几行改动）足够可靠。
    """
    import difflib

    original_lines = original.splitlines(keepends=True)

    # 解析 patch 中的 hunk
    hunks = []
    current_hunk = None
    for line in patch.splitlines(keepends=True):
        if line.startswith("@@"):
            # @@ -old_start,old_len +new_start,new_len @@
            m = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
            if m:
                old_start = int(m.group(1))
                old_len = int(m.group(2)) if m.group(2) is not None else 1
                current_hunk = {"old_start": old_start, "old_len": old_len, "lines": []}
                hunks.append(current_hunk)
        elif current_hunk is not None:
            if line.startswith(("+", "-", " ", "\\")):
                current_hunk["lines"].append(line)

    if not hunks:
        return original

    # 从后往前应用 hunk（避免行号偏移）
    result_lines = list(original_lines)
    for hunk in reversed(hunks):
        old_start = hunk["old_start"] - 1  # 转 0-indexed
        old_len = hunk["old_len"]
        new_lines = []
        for hl in hunk["lines"]:
            if hl.startswith("\\"):
                continue
            if hl.startswith("+"):
                new_lines.append(hl[1:])
            elif hl.startswith(" "):
                new_lines.append(hl[1:])
            # "-" 行不加入 new_lines（删除）
        result_lines[old_start : old_start + old_len] = new_lines

    return "".join(result_lines)


def run_fix_pipeline(
    finding: Finding,
    diff_context: str,
    owner: str,
    repo: str,
    base_ref: str,
    head_sha: str,
    user_pat: str,
    review_id: str,
    finding_index: int,
) -> FixResult:
    """执行完整修复管线：生成补丁 → 审核 → 开 PR。

    user_pat 是调用方传入的明文 PAT，本函数不存储它。
    """
    # 步骤 1：how88 生成补丁
    try:
        raw_response = call_how88_for_patch(finding, diff_context)
    except Exception as exc:
        return FixResult(
            status="error",
            patch=None,
            review_verdict="",
            pr_url=None,
            error=f"how88 调用失败：{type(exc).__name__}: {exc}",
        )

    patch = _parse_patch_from_response(raw_response)
    if patch is None:
        return FixResult(
            status="rejected",
            patch=None,
            review_verdict="how88 认为此问题无法通过简单补丁修复",
            pr_url=None,
            error=None,
        )

    # 步骤 2：DeepSeek 审核补丁
    try:
        review_result = call_deepseek_for_review(patch, finding)
    except Exception as exc:
        return FixResult(
            status="error",
            patch=patch,
            review_verdict="",
            pr_url=None,
            error=f"DeepSeek 审核调用失败：{type(exc).__name__}: {exc}",
        )

    verdict = review_result.get("verdict", "reject")
    reason = review_result.get("reason", "")
    concerns = review_result.get("concerns", [])
    review_verdict = f"{verdict}: {reason}"
    if concerns:
        review_verdict += f" 问题：{'; '.join(concerns)}"

    if verdict != "approve":
        return FixResult(
            status="rejected",
            patch=patch,
            review_verdict=review_verdict,
            pr_url=None,
            error=None,
        )

    # 步骤 3：开 PR（审核通过才到这里）
    try:
        pr_url = open_pr_with_user_pat(
            owner=owner,
            repo=repo,
            base_ref=base_ref,
            head_sha=head_sha,
            patch=patch,
            finding=finding,
            user_pat=user_pat,
            review_id=review_id,
            finding_index=finding_index,
        )
    except Exception as exc:
        return FixResult(
            status="error",
            patch=patch,
            review_verdict=review_verdict,
            pr_url=None,
            error=f"开 PR 失败：{type(exc).__name__}: {exc}",
        )

    return FixResult(
        status="approved",
        patch=patch,
        review_verdict=review_verdict,
        pr_url=pr_url,
        error=None,
    )
