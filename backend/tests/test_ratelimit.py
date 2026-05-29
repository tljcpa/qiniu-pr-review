"""IP 限流器单元测试（纯逻辑，注入 now 控制时间，无需真实等待）。"""

from app.core.ratelimit import RateLimiter, client_ip


class _Req:
    def __init__(self, headers=None, host="1.2.3.4"):
        self.headers = headers or {}

        class _C:
            pass

        self.client = _C()
        self.client.host = host


def test_allow_under_limit():
    rl = RateLimiter(max_calls=3, window=60)
    assert rl.allow("ip", now=0.0)
    assert rl.allow("ip", now=1.0)
    assert rl.allow("ip", now=2.0)


def test_block_over_limit():
    rl = RateLimiter(max_calls=3, window=60)
    rl.allow("ip", now=0.0)
    rl.allow("ip", now=1.0)
    rl.allow("ip", now=2.0)
    # 第 4 次在窗口内 -> 拒绝
    assert rl.allow("ip", now=3.0) is False


def test_window_slides():
    rl = RateLimiter(max_calls=2, window=60)
    rl.allow("ip", now=0.0)
    rl.allow("ip", now=1.0)
    assert rl.allow("ip", now=2.0) is False
    # 等过窗口后旧的两次过期 -> 又能放行
    assert rl.allow("ip", now=62.0) is True


def test_per_key_isolated():
    rl = RateLimiter(max_calls=1, window=60)
    assert rl.allow("a", now=0.0)
    assert rl.allow("a", now=1.0) is False
    # 不同 IP 互不影响
    assert rl.allow("b", now=1.0) is True


def test_client_ip_prefers_xff():
    req = _Req(headers={"x-forwarded-for": "9.9.9.9, 10.0.0.1"}, host="127.0.0.1")
    assert client_ip(req) == "9.9.9.9"


def test_client_ip_fallback_to_client_host():
    req = _Req(headers={}, host="5.6.7.8")
    assert client_ip(req) == "5.6.7.8"
