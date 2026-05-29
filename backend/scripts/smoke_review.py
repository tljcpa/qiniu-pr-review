"""端到端 review 实测（M-02 里程碑）：真实 PR -> 拉取 -> 分层上下文 -> 两段式路由 -> 打印结果。

会真实调用 DeepSeek（chat + reasoner），消耗额度。
用法：cd backend && ../.venv/bin/python scripts/smoke_review.py [PR_URL]
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.context_builder import ContextBuilder  # noqa: E402
from app.services.github_fetcher import GitHubFetcher  # noqa: E402
from app.services.router import ReviewRouter  # noqa: E402


def main():
    url = sys.argv[1] if len(sys.argv) > 1 else "https://github.com/psf/requests/pull/7487"
    print(f"=== 端到端 review: {url} ===\n")

    t0 = time.time()
    fetcher = GitHubFetcher()
    pr = fetcher.fetch(url)
    print(f"[1/3] 拉取完成: {pr.title}  (+{pr.additions} -{pr.deletions}, {pr.changed_files_count} 文件)")

    bundle = ContextBuilder(fetcher=fetcher).build(pr)
    print(f"[2/3] 上下文构建: 层级={bundle.level.value}, ~{bundle.total_tokens} token")

    review = ReviewRouter().review(bundle)
    elapsed = time.time() - t0

    print(f"[3/3] review 完成, 用时 {elapsed:.1f}s\n")
    print(f"--- 总结 ---\n{review.summary}\n")
    print(f"--- 发现 {len(review.findings)} 条问题 ---")
    for i, f in enumerate(review.findings, 1):
        deep = "深读" if f.deep_read else "未深读"
        print(f"\n[{i}] [{f.severity}/{f.category}] {f.title}  ({f.verdict}, {deep})")
        print(f"    位置: {f.file} {f.line_hint}")
        print(f"    说明: {f.detail[:200]}")
        print(f"    建议: {f.suggestion[:160]}")
        if f.reasoning:
            print(f"    思维链(前240字): {f.reasoning[:240]} ...")

    print(f"\n--- 过程轨迹 ---")
    for t in review.trace:
        print(f"  · {t}")
    print(f"\n--- token 用量 ---\n  {review.usage}")
    print("\n[OK]")


if __name__ == "__main__":
    main()
