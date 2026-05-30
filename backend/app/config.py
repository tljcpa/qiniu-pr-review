"""全局配置。

设计要点：
- 用 pydantic-settings 从环境变量 / .env 读取，类型校验在启动期完成，
  避免运行到一半才发现 key 没配。
- 不在代码里写任何真实密钥，全部来自环境（CI / Docker / .env）。
- .env 路径用绝对路径解析（仓库根 + backend 目录都找），不依赖启动时的 cwd。
  否则从 backend/ 起 uvicorn 时相对 ".env" 找不到仓库根的 .env，key 静默为空（见复盘 L-03）。
"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# 本文件位于 <repo>/backend/app/config.py
_APP_DIR = Path(__file__).resolve().parent          # <repo>/backend/app
_BACKEND_DIR = _APP_DIR.parent                       # <repo>/backend
_REPO_ROOT = _BACKEND_DIR.parent                     # <repo>

# 候选 .env 位置：仓库根优先（部署放这里），其次 backend 目录（本地开发可能放这）
_ENV_CANDIDATES = [
    str(_REPO_ROOT / ".env"),
    str(_BACKEND_DIR / ".env"),
]


class Settings(BaseSettings):
    # env_file 接受多个候选路径（绝对路径），按顺序加载；都不存在则仅用真实环境变量。
    # extra=ignore 容忍 .env 里有本程序不认识的变量。
    model_config = SettingsConfigDict(
        env_file=_ENV_CANDIDATES,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- DeepSeek（默认后端）----
    deepseek_api_key: str = ""
    # 注意：base_url 不带 /v1，openai SDK 会自动补全
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_chat_model: str = "deepseek-chat"
    deepseek_reasoner_model: str = "deepseek-reasoner"

    # ---- Azure OpenAI（备用 / 交叉验证）----
    azure_openai_endpoint: str = ""
    azure_openai_api_key: str = ""
    azure_openai_api_version: str = ""
    azure_openai_deployment: str = ""

    # ---- GitHub ----
    gh_token: str = ""

    # ---- 用户系统（PR46）----
    # JWT 签名密钥：生产必须换成随机高熵字符串（openssl rand -hex 32）
    jwt_secret: str = "CHANGE_ME_in_production"
    jwt_expire_hours: int = 72
    # SQLite 数据库文件路径（相对路径从项目根解析）
    db_path: str = str(_REPO_ROOT / "pr_review.db")
    # PAT 加密主密钥（hex 编码的 32 字节 AES key）：生产必须换
    # openssl rand -hex 32
    pat_encrypt_key: str = "0" * 64  # 占位，.env 里覆盖

    # ---- 服务参数 ----
    app_host: str = "0.0.0.0"
    app_port: int = 8080
    # CORS 白名单（逗号分隔）。默认收紧为线上域 + 本地开发域，不再用 *（见复盘 D-27）。
    # 本项目无 Cookie/凭证鉴权，CORS 配合 allow_credentials=False 使用。
    cors_origins: str = (
        "https://pr.qiniu.zdwktlj.top,"
        "http://localhost:5173,http://127.0.0.1:5173,"
        "http://localhost:4173,http://127.0.0.1:4173"
    )

    # ---- 上下文工程参数（PR5；阈值是初值，可按实测调，见复盘 D-05）----
    # 喂给模型的上下文 token 预算（给输出留余量后的输入上限）
    context_token_budget: int = 24000
    # 总改动行数 < 此值走 L2（整文件全文）
    context_l2_max_lines: int = 800
    # 总改动行数 <= 此值走 L3（抽取式上下文）；超过走 L4（仅 diff）
    context_l3_max_lines: int = 3000

    # ---- 限流参数（D-27；公开 POST /api/review 防刷，保护 LLM 余额）----
    # 每个 IP 在 rate_limit_window 秒内最多发起 rate_limit_max 次 review
    rate_limit_max: int = 10
    rate_limit_window: int = 60

    def cors_origin_list(self) -> list[str]:
        """把逗号分隔的 CORS_ORIGINS 拆成列表。"""
        if self.cors_origins.strip() == "*":
            return ["*"]
        result = []
        for item in self.cors_origins.split(","):
            cleaned = item.strip()
            if cleaned:
                result.append(cleaned)
        return result


# 单例：全应用共享一份配置
settings = Settings()
