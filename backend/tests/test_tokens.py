"""token 估计启发式测试。"""

from app.services.tokens import estimate_messages_tokens, estimate_tokens


def test_empty():
    assert estimate_tokens("") == 0


def test_ascii_roughly_quarter():
    # 40 个 ASCII 字符约 10 token
    text = "a" * 40
    assert 8 <= estimate_tokens(text) <= 12


def test_cjk_roughly_one_per_char():
    # 10 个汉字约 10 token（保守略高）
    text = "测试中文字符数量估计准确" # 12 字
    assert 10 <= estimate_tokens(text) <= 16


def test_messages_overhead():
    msgs = [
        {"role": "system", "content": "你是评审"},
        {"role": "user", "content": "hello"},
    ]
    # 含每条 4 token 开销
    assert estimate_messages_tokens(msgs) > estimate_tokens("你是评审") + estimate_tokens("hello")


def test_monotonic():
    short = estimate_tokens("abc")
    long = estimate_tokens("abc" * 100)
    assert long > short
