"""分层上下文实测：拉真实 PR -> 构建上下文 -> 打印层级/预算/裁剪情况。

用法：cd backend && ../.venv/bin/python scripts/smoke_context.py [PR_URL]
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.context_builder import ContextBuilder  # noqa: E402
from app.services.github_fetcher import GitHubFetcher  # noqa: E402


def main():
    url = sys.argv[1] if len(sys.argv) > 1 else "https://github.com/psf/requests/pull/7487"
    fetcher = GitHubFetcher()
    pr = fetcher.fetch(url)
    bundle = ContextBuilder(fetcher=fetcher).build(pr)

    print(f"PR: {pr.title}")
    print(f"总改动行: {pr.total_changed_lines}  -> 层级: {bundle.level.value}")
    print(f"预算: {bundle.budget}  实际估计 token: {bundle.total_tokens}")
    print(f"文件数: {len(bundle.files)}")
    for fc in bundle.files:
        print(f"  - {fc.filename}: 富化={fc.enrichment_kind}, ~{fc.token_estimate} token")
    if bundle.truncated_notes:
        print("裁剪声明:")
        for n in bundle.truncated_notes:
            print(f"  ! {n}")
    prompt = bundle.to_prompt_text()
    print(f"\n最终 prompt 文本长度: {len(prompt)} 字符")
    print("=== prompt 前 600 字符 ===")
    print(prompt[:600])
    print("\n[OK]")


if __name__ == "__main__":
    main()
