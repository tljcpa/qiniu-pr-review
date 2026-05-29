"""LLM 抽象层单元测试（不打真实网络，用 stub client 注入）。

覆盖：
1. 正常返回的归一化解析（content / usage / finish_reason）
2. reasoning_content 提取：直接字段 + model_extra 兜底两条路
3. 退避重试：瞬时错误先失败后成功
4. 重试耗尽 -> LLMError
5. 不可重试错误 -> 立即 LLMError
"""

import types

import httpx
import pytest
from openai import APITimeoutError

from app.services.llm_provider import DeepSeekProvider, LLMError


def _make_response(content="ok", reasoning=None, use_model_extra=False):
    """构造一个仿 openai SDK 的返回对象。"""
    message = types.SimpleNamespace(content=content)
    if reasoning is not None and not use_model_extra:
        message.reasoning_content = reasoning
    if use_model_extra:
        # 模拟 SDK 把未知字段塞进 model_extra 的情况
        message.model_extra = {"reasoning_content": reasoning}
    choice = types.SimpleNamespace(message=message, finish_reason="stop")
    usage = types.SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15)
    return types.SimpleNamespace(choices=[choice], usage=usage, model="deepseek-chat")


class _StubCompletions:
    def __init__(self, behaviors):
        # behaviors: 每次调用按顺序消费；元素是"要抛的异常"或"要返回的对象"
        self._behaviors = list(behaviors)
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        item = self._behaviors.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class _StubClient:
    def __init__(self, behaviors):
        self.chat = types.SimpleNamespace(completions=_StubCompletions(behaviors))


def _timeout_error():
    # APITimeoutError 只需一个 request 即可构造，属于可重试错误
    return APITimeoutError(request=httpx.Request("POST", "https://api.deepseek.com/x"))


def _provider(behaviors, **kwargs):
    stub = _StubClient(behaviors)
    # base_delay=0 让退避不真正睡，测试瞬间完成
    prov = DeepSeekProvider(model="deepseek-chat", client=stub, base_delay=0.0, **kwargs)
    return prov, stub


def test_normal_parse():
    prov, stub = _provider([_make_response(content="hello")])
    resp = prov.complete([{"role": "user", "content": "hi"}])
    assert resp.content == "hello"
    assert resp.reasoning_content is None
    assert resp.usage["total_tokens"] == 15
    assert resp.finish_reason == "stop"
    assert resp.provider == "deepseek"
    assert stub.chat.completions.calls == 1


def test_reasoning_direct_field():
    prov, _ = _provider([_make_response(reasoning="一步步想：先看边界")])
    resp = prov.complete([{"role": "user", "content": "hi"}])
    assert resp.reasoning_content == "一步步想：先看边界"


def test_reasoning_from_model_extra():
    prov, _ = _provider([_make_response(reasoning="放在 extra 里", use_model_extra=True)])
    resp = prov.complete([{"role": "user", "content": "hi"}])
    assert resp.reasoning_content == "放在 extra 里"


def test_retry_then_success():
    # 前两次超时，第三次成功；max_retries=3 足够
    behaviors = [_timeout_error(), _timeout_error(), _make_response(content="终于好了")]
    prov, stub = _provider(behaviors, max_retries=3)
    resp = prov.complete([{"role": "user", "content": "hi"}])
    assert resp.content == "终于好了"
    assert stub.chat.completions.calls == 3


def test_retry_exhausted():
    # 全部超时，max_retries=2 -> 共 3 次尝试后抛 LLMError
    behaviors = [_timeout_error(), _timeout_error(), _timeout_error()]
    prov, stub = _provider(behaviors, max_retries=2)
    with pytest.raises(LLMError):
        prov.complete([{"role": "user", "content": "hi"}])
    assert stub.chat.completions.calls == 3


def test_non_retryable_raises_immediately():
    # 普通 ValueError 代表不可重试错误，应只尝试一次就抛 LLMError
    prov, stub = _provider([ValueError("bad params")], max_retries=3)
    with pytest.raises(LLMError):
        prov.complete([{"role": "user", "content": "hi"}])
    assert stub.chat.completions.calls == 1
