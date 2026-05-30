"""用户 ORM 模型。

字段说明：
- password_hash：bcrypt 慢哈希，绝不存明文
- github_pat_enc：用户的 fine-grained PAT，AES-256-GCM 加密后的 hex 字符串；
  可空（未绑定时为 None）；绝不在任何 API 响应或日志中返回明文
- github_username：绑定 PAT 时从 GitHub API 验证取回，非敏感，用于前端展示
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(120))
    # GitHub PAT（AES-256-GCM 加密存储；None = 未绑定）
    github_pat_enc: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    # 绑定 PAT 后验证得到的 GitHub 用户名（非敏感，前端展示用）
    github_username: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
