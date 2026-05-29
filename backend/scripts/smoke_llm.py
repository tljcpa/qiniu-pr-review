"""LLM 抽象层实测脚本（会真实调用 API，消耗少量额度）。

用法（在仓库根 source 好 .env 或导出环境变量后）：
    cd backend && ../.venv/bin/python scripts/smoke_llm.py

逐个验证三个角色的 provider：chat 快扫、reasoner 深读（看思维链）、azure 交叉验证。
每个都用极短 prompt，把额度消耗压到最低。
"""

import sys
from pathlib import Path

# 让脚本能 import 到 app 包
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.llm_provider import (  # noqa: E402
    get_chat_provider,
    get_reasoner_provider,
    get_verifier_provider,
)


def _run(name, provider, messages):
    print(f"\n===== {name} ({provider.model}) =====")
    try:
        resp = provider.complete(messages, max_tokens=200)
    except Exception as exc:
        print(f"[FAIL] {type(exc).__name__}: {exc}")
        return False
    print(f"content     : {resp.content[:120]}")
    if resp.reasoning_content:
        print(f"reasoning   : {resp.reasoning_content[:160]} ...")
    print(f"usage       : {resp.usage}")
    print(f"finish      : {resp.finish_reason}")
    print("[OK]")
    return True


def main():
    msgs = [{"role": "user", "content": "只回一个词：pong"}]
    reason_msgs = [{"role": "user", "content": "1+1 等于几？简短回答。"}]

    results = []
    results.append(_run("DeepSeek chat 快扫", get_chat_provider(), msgs))
    results.append(_run("DeepSeek reasoner 深读", get_reasoner_provider(), reason_msgs))
    # Azure 可能未配置，单独 try
    try:
        results.append(_run("Azure 交叉验证", get_verifier_provider(), msgs))
    except Exception as exc:
        print(f"\n[Azure 跳过] {type(exc).__name__}: {exc}")

    print(f"\n==== 通过 {sum(results)}/{len(results)} ====")


if __name__ == "__main__":
    main()
