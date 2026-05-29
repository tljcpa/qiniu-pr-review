"""全局配置。

设计要点：
- 用 pydantic-settings 从环境变量 / .env 读取，类型校验在启动期完成，
  避免运行到一半才发现 key 没配。
- 不在代码里写任何真实密钥，全部来自环境（CI / Docker / .env）。
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # env_file 指向仓库根的 .env；extra=ignore 容忍 .env 里有本程序不认识的变量
    model_config = SettingsConfigDict(
        env_file=".env",
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

    # ---- 服务参数 ----
    app_host: str = "0.0.0.0"
    app_port: int = 8080
    cors_origins: str = "*"

    # ---- 上下文工程参数（PR5；阈值是初值，可按实测调，见复盘 D-05）----
    # 喂给模型的上下文 token 预算（给输出留余量后的输入上限）
    context_token_budget: int = 24000
    # 总改动行数 < 此值走 L2（整文件全文）
    context_l2_max_lines: int = 800
    # 总改动行数 <= 此值走 L3（抽取式上下文）；超过走 L4（仅 diff）
    context_l3_max_lines: int = 3000

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
