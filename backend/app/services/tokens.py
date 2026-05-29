"""轻量 token 估计（确定性启发式，零依赖）。

为什么不用 tiktoken（详见复盘 D-14）：DeepSeek 不是 OpenAI tokenizer，tiktoken
对它本就不准；我们只需要"够准的预算控制"，不需要精确计费。

启发式：
- 英文/ASCII：约 4 个字符 ≈ 1 token
- 中文/CJK：约 1 个字符 ≈ 1 token（汉字基本一字一 token 甚至更多，取保守）
分别统计两类字符数，加权求和，向上取整。宁可略高估，避免超预算。
"""

from __future__ import annotations

import math

# ASCII 文本的字符/ token 比
_ASCII_CHARS_PER_TOKEN = 4.0
# CJK 字符的字符/ token 比（一字约一 token，保守取略小于 1 让估计偏高一点）
_CJK_CHARS_PER_TOKEN = 1.0


def _is_cjk(ch: str) -> bool:
    """粗判一个字符是否属于中日韩等"宽字符"区间。"""
    code = ord(ch)
    # 常见 CJK 统一表意文字 + 扩展A + 兼容 + 假名 + 韩文音节
    if 0x3000 <= code <= 0x9FFF:
        return True
    if 0xAC00 <= code <= 0xD7A3:
        return True
    if 0xF900 <= code <= 0xFAFF:
        return True
    if 0x20000 <= code <= 0x2FA1F:
        return True
    return False


def estimate_tokens(text: str) -> int:
    """估算一段文本的 token 数（偏保守，宁高勿低）。"""
    if not text:
        return 0
    cjk = 0
    other = 0
    for ch in text:
        if _is_cjk(ch):
            cjk += 1
        else:
            other += 1
    estimate = cjk / _CJK_CHARS_PER_TOKEN + other / _ASCII_CHARS_PER_TOKEN
    return int(math.ceil(estimate))


def estimate_messages_tokens(messages: list[dict]) -> int:
    """估算 OpenAI 格式 messages 的总 token（含每条约 4 token 的角色开销）。"""
    total = 0
    for msg in messages:
        total += estimate_tokens(msg.get("content", ""))
        # 每条消息的 role/分隔符固定开销，经验值约 4 token
        total += 4
    return total
