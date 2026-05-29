"""FastAPI 应用入口。

PR1 阶段只暴露一个健康检查端点，保证 main 分支从骨架起就可运行、可部署。
后续 PR 会在 app/api 下挂载 review 相关路由。
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.config import settings


def create_app() -> FastAPI:
    """应用工厂：便于测试时构造独立实例。"""
    application = FastAPI(
        title="AI PR Review 助手",
        description="基于大模型的 GitHub Pull Request 智能评审工具",
        version=__version__,
    )

    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list(),
        allow_credentials=True,
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

    return application


app = create_app()
