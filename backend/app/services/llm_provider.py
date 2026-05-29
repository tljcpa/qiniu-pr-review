"""LLM 抽象层。

目标：屏蔽 DeepSeek 与 Azure OpenAI 的差异，给上层（路由、交叉验证）一个统一接口。
统一返回 LLMResponse：content + reasoning_content（思维链）+ usage（token）+ 元信息。

设计要点（详见复盘 D-04 / D-04b）：
- 用 openai-python SDK：DeepSeek 兼容 OpenAI 协议，Azure 有原生 client，一套通吃。
- 同步实现：调用都是请求-响应式，FastAPI 里用 run_in_threadpool 不阻塞事件循环。
- 内建指数退避：限流（RateLimitError）、连接/超时、5xx 都重试，区分"可重试"与"不可重试"错误。
- reasoning_content：DeepSeek reasoner 把思维链放在 message.reasoning_content，
  SDK 未必把它列为正式字段，统一用 getattr + model_extra 兜底提取。
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field

from openai import (
    APIConnectionError,
    APITimeoutError,
    AzureOpenAI,
    InternalServerError,
    OpenAI,
    RateLimitError,
)

from app.config import settings


# 这些异常代表"瞬时故障"，值得退避后重试；其余（如 401 鉴权错、400 参数错）立即抛出
_RETRYABLE_ERRORS = (
    RateLimitError,
    APIConnectionError,
    APITimeoutError,
    InternalServerError,
)


class LLMError(Exception):
    """LLM 调用最终失败（重试耗尽或不可重试错误）时抛出，附带 provider 上下文。"""


@dataclass
class LLMResponse:
    """一次 LLM 调用的统一结果。"""

    content: str
    # 思维链：仅 reasoner 类模型返回，普通 chat 模型为 None
    reasoning_content: str | None = None
    model: str = ""
    provider: str = ""
    # token 用量：{"prompt_tokens": x, "completion_tokens": y, "total_tokens": z}
    usage: dict = field(default_factory=dict)
    # 模型自报的结束原因（stop / length 等），length 说明被截断，上层需警惕
    finish_reason: str | None = None


def _extract_reasoning(message) -> str | None:
    """从返回的 message 里提取 reasoning_content。

    DeepSeek reasoner 把思维链放在 message.reasoning_content，但 openai SDK 的
    类型定义里没有这个字段，会落到 model_extra 里。两条路都试一遍。
    """
    direct = getattr(message, "reasoning_content", None)
    if direct:
        return direct
    extra = getattr(message, "model_extra", None)
    if extra and isinstance(extra, dict):
        value = extra.get("reasoning_content")
        if value:
            return value
    return None


class BaseLLMProvider:
    """所有 provider 的基类：封装"带退避重试的一次 chat completion"。

    子类只需提供 self._client（openai 兼容 client）、self._model、self.provider_name。
    """

    provider_name: str = "base"

    def __init__(self, model: str, *, max_retries: int = 4, base_delay: float = 1.0) -> None:
        self._model = model
        # 最多重试次数（不含首次）；总尝试 = max_retries + 1
        self._max_retries = max_retries
        # 退避基数（秒），实际延迟 = base_delay * 2**attempt + 抖动
        self._base_delay = base_delay
        # 子类负责赋值
        self._client = None

    @property
    def model(self) -> str:
        return self._model

    def _sleep_seconds(self, attempt: int) -> float:
        """计算第 attempt 次重试前要睡多久：指数退避 + 随机抖动避免惊群。"""
        backoff = self._base_delay * (2 ** attempt)
        jitter = random.uniform(0, self._base_delay)
        return backoff + jitter

    def complete(
        self,
        messages: list[dict],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        extra_body: dict | None = None,
    ) -> LLMResponse:
        """发起一次 chat completion，带指数退避重试。

        messages: OpenAI 格式 [{"role": "system"/"user"/"assistant", "content": "..."}]
        temperature: 默认 0，代码评审要稳定可复现，不要发散
        """
        if self._client is None:
            raise LLMError(f"{self.provider_name}: client 未初始化")

        last_error: Exception | None = None
        # 总尝试次数 = 首次 + max_retries 次重试
        for attempt in range(self._max_retries + 1):
            try:
                return self._do_call(messages, temperature, max_tokens, extra_body)
            except _RETRYABLE_ERRORS as exc:
                last_error = exc
                # 最后一次失败就不再睡，直接跳出去抛
                if attempt < self._max_retries:
                    time.sleep(self._sleep_seconds(attempt))
                    continue
                break
            except Exception as exc:
                # 不可重试错误（鉴权、参数等）：立即包装抛出，不浪费重试
                raise LLMError(
                    f"{self.provider_name} 调用失败（不可重试）: {type(exc).__name__}: {exc}"
                ) from exc

        raise LLMError(
            f"{self.provider_name} 调用失败（重试 {self._max_retries} 次仍失败）: "
            f"{type(last_error).__name__}: {last_error}"
        ) from last_error

    def _do_call(
        self,
        messages: list[dict],
        temperature: float,
        max_tokens: int | None,
        extra_body: dict | None,
    ) -> LLMResponse:
        """真正发请求并把 SDK 返回归一化成 LLMResponse。"""
        kwargs: dict = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if extra_body is not None:
            kwargs["extra_body"] = extra_body

        resp = self._client.chat.completions.create(**kwargs)
        choice = resp.choices[0]
        message = choice.message

        usage = {}
        if resp.usage is not None:
            usage = {
                "prompt_tokens": resp.usage.prompt_tokens,
                "completion_tokens": resp.usage.completion_tokens,
                "total_tokens": resp.usage.total_tokens,
            }

        return LLMResponse(
            content=message.content or "",
            reasoning_content=_extract_reasoning(message),
            model=resp.model or self._model,
            provider=self.provider_name,
            usage=usage,
            finish_reason=choice.finish_reason,
        )


class DeepSeekProvider(BaseLLMProvider):
    """DeepSeek 后端（默认）。chat 与 reasoner 都用它，只是 model 不同。"""

    provider_name = "deepseek"

    def __init__(self, model: str | None = None, *, client=None, **kwargs) -> None:
        super().__init__(model or settings.deepseek_chat_model, **kwargs)
        # 允许注入 client（测试用 stub）；否则按配置建真 client
        if client is not None:
            self._client = client
        else:
            self._client = OpenAI(
                api_key=settings.deepseek_api_key,
                base_url=settings.deepseek_base_url,
            )


class AzureProvider(BaseLLMProvider):
    """Azure OpenAI 后端（GPT-4.1-mini），用于高风险 finding 的交叉验证。"""

    provider_name = "azure"

    def __init__(self, model: str | None = None, *, client=None, **kwargs) -> None:
        # Azure 用 deployment 名当 model
        super().__init__(model or settings.azure_openai_deployment, **kwargs)
        if client is not None:
            self._client = client
        else:
            self._client = AzureOpenAI(
                api_key=settings.azure_openai_api_key,
                api_version=settings.azure_openai_api_version,
                azure_endpoint=settings.azure_openai_endpoint,
            )


# ---- 便捷工厂：按角色拿 provider，路由层直接用 ----

def get_chat_provider() -> DeepSeekProvider:
    """第一遍快扫用的便宜模型。"""
    return DeepSeekProvider(settings.deepseek_chat_model)


def get_reasoner_provider() -> DeepSeekProvider:
    """第二遍深读用的推理模型（返回 reasoning_content）。"""
    return DeepSeekProvider(settings.deepseek_reasoner_model)


def get_verifier_provider() -> AzureProvider:
    """第三遍交叉验证用的异构模型（GPT-4.1-mini）。"""
    return AzureProvider(settings.azure_openai_deployment)
