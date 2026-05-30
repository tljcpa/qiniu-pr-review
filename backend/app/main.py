"""FastAPI 应用入口。

PR1 阶段只暴露一个健康检查端点，保证 main 分支从骨架起就可运行、可部署。
后续 PR 会在 app/api 下挂载 review 相关路由。
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.config import settings
from app.db import init_db


def create_app() -> FastAPI:
    """应用工厂：便于测试时构造独立实例。"""
    # 启动时建表（幂等，已存在不重建）
    init_db()

    application = FastAPI(
        title="AI PR Review 助手",
        description="基于大模型的 GitHub Pull Request 智能评审工具",
        version=__version__,
    )

    # CORS：keep allow_credentials=False（见复盘 D-27）。
    # Bearer token 通过显式 Authorization 头发送，不依赖浏览器 credentials 机制（cookies）；
    # 前端 fetch 不需要 credentials:'include'，所以 False 是正确且更安全的选择。
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list(),
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @application.get("/api/health", tags=["meta"])
    def health() -> dict:
        """健康检查：部署探活与本地冒烟测试都用它。"""
        return {
            "status": "ok",
            "service": "pr-review",
            "version": __version__,
        }

    # 挂载认证路由（PR46）
    from app.api.auth import router as auth_router

    application.include_router(auth_router)

    # 挂载用户设置路由（PR47：PAT 绑定）
    from app.api.user import router as user_router

    application.include_router(user_router)

    # 挂载 review 路由（PR9）
    from app.api.review import router as review_router

    application.include_router(review_router)

    return application


app = create_app()
