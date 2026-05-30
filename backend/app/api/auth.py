"""用户认证 API：注册 + 登录。

端点：
    POST /api/auth/register  注册新用户
    POST /api/auth/login     登录，返回 JWT
    GET  /api/auth/me        查看当前登录用户信息（需 Bearer token）

安全约定：
    - 密码只存 bcrypt 哈希，任何响应都不返回密码字段
    - PAT 加密存储，任何响应都不返回明文 PAT
    - 错误信息对外统一，不暴露"用户存在/不存在"等细节
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.auth import create_token, get_current_user, hash_password, verify_password
from app.db import get_session
from app.models.user import User

router = APIRouter(prefix="/api/auth", tags=["auth"])


# ---------- 请求/响应 schema ----------

class RegisterRequest(BaseModel):
    username: str = Field(min_length=2, max_length=50)
    password: str = Field(min_length=6, max_length=128)


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: int
    username: str


class MeResponse(BaseModel):
    user_id: int
    username: str
    github_username: str | None


# ---------- 端点实现 ----------

@router.post("/register", response_model=TokenResponse, status_code=201)
def register(req: RegisterRequest, session: Session = Depends(get_session)) -> TokenResponse:
    """注册新用户，成功后直接返回 JWT（省去再登录一步）。

    username 若已存在返回 409，不透露具体原因（仅提示"用户名已被使用"）。
    """
    user = User(
        username=req.username.strip(),
        password_hash=hash_password(req.password),
    )
    session.add(user)
    try:
        session.commit()
        session.refresh(user)
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=409, detail="用户名已被使用")

    token = create_token(user.id)
    return TokenResponse(
        access_token=token,
        user_id=user.id,
        username=user.username,
    )


@router.post("/login", response_model=TokenResponse)
def login(req: LoginRequest, session: Session = Depends(get_session)) -> TokenResponse:
    """登录，凭 username+password 换 JWT。

    用户名不存在和密码错误都返回同一个 401，防止用户枚举攻击。
    """
    user = session.query(User).filter(User.username == req.username).first()
    # 用户不存在时也跑一次 verify（防止时序攻击）
    dummy_hash = "$2b$12$invalidhashpadding000000000000000000000000000000000000000"
    ok = verify_password(req.password, user.password_hash if user else dummy_hash)
    if not ok or user is None:
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    token = create_token(user.id)
    return TokenResponse(
        access_token=token,
        user_id=user.id,
        username=user.username,
    )


@router.get("/me", response_model=MeResponse)
def me(current_user: User = Depends(get_current_user)) -> MeResponse:
    """返回当前登录用户的非敏感信息。"""
    return MeResponse(
        user_id=current_user.id,
        username=current_user.username,
        github_username=current_user.github_username,
    )
