"""用户设置 API：GitHub PAT 绑定与查询。

端点：
    PUT  /api/user/github-pat   绑定/更新 GitHub fine-grained PAT
    GET  /api/user/me           查看非敏感用户信息（复用 auth.me 逻辑）
    DELETE /api/user/github-pat 解绑 PAT

安全铁律：
    - PAT 以 AES-256-GCM 加密后存库，任何响应都不返回明文 PAT
    - 绑定时向 GitHub API 验证 PAT 有效性，同时取回 github_username 存库
    - 验证失败（PAT 无效/过期）返回 422，不存入数据库
"""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.pat_crypto import decrypt_pat, encrypt_pat
from app.db import get_session
from app.models.user import User

router = APIRouter(prefix="/api/user", tags=["user"])


class PATRequest(BaseModel):
    # PAT 最短 20 字节（GitHub fine-grained 格式 github_pat_...），最长 200
    pat: str = Field(min_length=20, max_length=200)


class PATResponse(BaseModel):
    ok: bool
    github_username: str


class MeResponse(BaseModel):
    user_id: int
    username: str
    github_username: str | None
    has_pat: bool


def _verify_github_pat(pat: str) -> str:
    """用 PAT 调 GitHub /user，验证有效性并返回 github_username。

    失败（401/403/网络错误）抛 HTTPException 422。
    """
    try:
        resp = httpx.get(
            "https://api.github.com/user",
            headers={
                "Authorization": f"token {pat}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=10.0,
        )
    except httpx.RequestError as exc:
        raise HTTPException(status_code=422, detail=f"无法连接 GitHub API：{exc}") from exc

    if resp.status_code == 401:
        raise HTTPException(status_code=422, detail="PAT 无效或已过期，请检查后重试")
    if resp.status_code == 403:
        raise HTTPException(status_code=422, detail="PAT 权限不足（需要 repo 读权限）")
    if resp.status_code != 200:
        raise HTTPException(status_code=422, detail=f"GitHub API 返回异常状态：{resp.status_code}")

    data = resp.json()
    login = data.get("login")
    if not login:
        raise HTTPException(status_code=422, detail="无法从 GitHub API 取得用户名")
    return login


@router.put("/github-pat", response_model=PATResponse)
def bind_pat(
    req: PATRequest,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> PATResponse:
    """绑定或更新用户的 GitHub fine-grained PAT。

    流程：验证 PAT 有效 → 取 github_username → AES 加密 → 存库。
    任何步骤失败都不改写数据库。
    """
    # 验证 PAT 有效性（会调 GitHub API）
    github_username = _verify_github_pat(req.pat)

    # 加密后存库
    encrypted = encrypt_pat(req.pat)
    current_user.github_pat_enc = encrypted
    current_user.github_username = github_username
    session.add(current_user)
    session.commit()

    return PATResponse(ok=True, github_username=github_username)


@router.delete("/github-pat")
def unbind_pat(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> dict:
    """解绑 PAT（清除加密字段和 github_username）。"""
    current_user.github_pat_enc = None
    current_user.github_username = None
    session.add(current_user)
    session.commit()
    return {"ok": True}


@router.get("/me", response_model=MeResponse)
def me(current_user: User = Depends(get_current_user)) -> MeResponse:
    """返回当前用户的非敏感信息，包括是否已绑定 PAT。"""
    return MeResponse(
        user_id=current_user.id,
        username=current_user.username,
        github_username=current_user.github_username,
        has_pat=current_user.github_pat_enc is not None,
    )


def get_user_pat(user: User) -> str:
    """供其他服务层调用：取出当前用户的明文 PAT。

    未绑定或解密失败都抛 HTTPException 400（上层捕获后向用户解释）。
    这是唯一允许解密 PAT 的地方。
    """
    if not user.github_pat_enc:
        raise HTTPException(status_code=400, detail="请先绑定您的 GitHub PAT（设置 -> 绑定 GitHub PAT）")
    try:
        return decrypt_pat(user.github_pat_enc)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"PAT 解密失败，请重新绑定：{exc}") from exc
