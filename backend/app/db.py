"""SQLite 数据库层。

用 SQLAlchemy 2.x ORM；数据库文件路径由配置决定，默认 ./pr_review.db。
设计决策（见复盘 D-38）：
- 选 SQLite 而非纯内存：用户账号和 PAT 需要跨重启持久化；
  演示单机足够，不引 Postgres 增复杂度。
- 用 check_same_thread=False + 连接池：FastAPI 多线程环境下 SQLite 默认
  不允许跨线程，必须显式关闭该限制。
"""

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings

_db_path = Path(settings.db_path).resolve()
_engine = create_engine(
    f"sqlite:///{_db_path}",
    connect_args={"check_same_thread": False},
    # 连接池大小：演示单机 5 个线程够用
    pool_size=5,
    max_overflow=10,
)

SessionLocal = sessionmaker(bind=_engine, autocommit=False, autoflush=False)


class Base(DeclarativeBase):
    """所有 ORM Model 的公共基类。"""
    pass


def init_db() -> None:
    """在应用启动时建表（幂等）。"""
    # 导入所有 model 让 metadata 知道所有表结构
    import app.models.user  # noqa: F401
    Base.metadata.create_all(bind=_engine)


def get_session() -> Session:
    """FastAPI 依赖：请求周期内取一个数据库 session，用完关闭。"""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
