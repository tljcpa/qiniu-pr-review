"""认证工具：bcrypt 密码哈希 + JWT 签发/解析 + FastAPI 依赖。

设计决策（见复盘 D-38）：
- bcrypt 慢哈希：抵抗暴力破解，即使数据库泄露明文也不可逆
- JWT HS256：无状态，不需要 session 存储，演示单机足够
- get_current_user：FastAPI 依赖，解 Bearer token → User 对象；
  任何需要登录的端点只需 Depends(get_current_user) 即可
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
import jwt
from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_session
from app.models.user import User

_ALG = "HS256"


def hash_password(plain: str) -> str:
    """bcrypt 哈希密码（自动加盐）。返回可直接存库的字符串。"""
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """校验明文密码与 bcrypt 哈希是否匹配。"""
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def create_token(user_id: int) -> str:
    """为指定用户 ID 签发 JWT，过期时间由配置决定。"""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + timedelta(hours=settings.jwt_expire_hours),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=_ALG)


def decode_token(token: str) -> Optional[int]:
    """解 JWT，返回 user_id（int）；令牌无效或过期返回 None。"""
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[_ALG])
        return int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        return None


def get_current_user(
    authorization: Optional[str] = Header(default=None),
    session: Session = Depends(get_session),
) -> User:
    """FastAPI 依赖：从 Authorization: Bearer <token> 取当前登录用户。

    失败统一抛 401，不区分"token 无效"和"用户不存在"——
    避免让攻击者区分两种状态。
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="未登录，请先登录")
    token = authorization.split(" ", 1)[1].strip()
    user_id = decode_token(token)
    if user_id is None:
        raise HTTPException(status_code=401, detail="登录已失效，请重新登录")
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="登录已失效，请重新登录")
    return user
