"""AI 修复端点（PR48）。

端点：
    POST /api/review/{review_id}/fix/{finding_index}
        触发 AI 改码管线：how88 生成补丁 → DeepSeek 审核 → 开 PR

返回：
    FixResponse：patch / review_verdict / pr_url / status / error
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from app.api.review import _jobs  # 复用已有任务状态表
from app.api.user import get_user_pat
from app.core.auth import get_current_user
from app.models.user import User
from app.services.ai_fix_service import FixResult, run_fix_pipeline
from app.services.github_fetcher import GitHubFetcher, parse_pr_url

router = APIRouter(prefix="/api/review", tags=["fix"])


class FixResponse(BaseModel):
    status: str
    patch: str | None
    review_verdict: str
    pr_url: str | None
    error: str | None


@router.post("/{review_id}/fix/{finding_index}", response_model=FixResponse)
async def fix_finding(
    review_id: str,
    finding_index: int,
    current_user: User = Depends(get_current_user),
) -> FixResponse:
    """对指定 finding 触发 AI 改码闭环。

    需登录（Bearer token）且用户已绑定 GitHub PAT。
    管线：how88 生成补丁 → DeepSeek 审核 → 开 PR（审过才执行）。
    """
    # 取 review job
    entry = _jobs.get(review_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="review_id 不存在")
    job = entry["job"]
    if job.status != "done" or job.report is None:
        raise HTTPException(status_code=409, detail="审查尚未完成，无法触发修复")

    findings = job.report.findings
    if finding_index < 0 or finding_index >= len(findings):
        raise HTTPException(
            status_code=400,
            detail=f"finding_index 超出范围（共 {len(findings)} 条）",
        )
    finding = findings[finding_index]

    # 解出用户 PAT（唯一解密点，异常时抛 400）
    user_pat = get_user_pat(current_user)

    # 重新拉 PR 元信息（需要 owner/repo/base_ref/head_sha/diff）
    def _fetch_and_run() -> FixResult:
        pr = GitHubFetcher().fetch(job.req.url)
        parsed = parse_pr_url(job.req.url)
        owner, repo = parsed["owner"], parsed["repo"]

        # 验证 PAT 是该仓库的所有者（安全检查：只允许向自己的 repo 开 PR）
        if current_user.github_username and current_user.github_username.lower() != owner.lower():
            raise HTTPException(
                status_code=403,
                detail=(
                    f"您的 GitHub 账号 ({current_user.github_username}) "
                    f"与 PR 仓库所有者 ({owner}) 不一致，"
                    "AI 修复只能在您自己的仓库上操作"
                ),
            )

        # 找到这个 finding 对应文件的 diff 片段
        diff_context = ""
        for f in pr.files:
            if f.filename == finding.file and f.patch:
                diff_context = f.patch
                break
        if not diff_context:
            diff_context = f"(文件 {finding.file} 的 diff 不可用)"

        return run_fix_pipeline(
            finding=finding,
            diff_context=diff_context,
            owner=owner,
            repo=repo,
            base_ref=pr.base_ref,
            head_sha=pr.head_sha,
            user_pat=user_pat,
            review_id=review_id,
            finding_index=finding_index,
        )

    try:
        result: FixResult = await run_in_threadpool(_fetch_and_run)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"修复管线异常：{type(exc).__name__}: {exc}") from exc

    return FixResponse(
        status=result.status,
        patch=result.patch,
        review_verdict=result.review_verdict,
        pr_url=result.pr_url,
        error=result.error,
    )
