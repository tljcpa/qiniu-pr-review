"""端到端实测 PR 回写：真实 review demo PR -> 写回 inline 批注 + summary review。

用法：cd backend && ../.venv/bin/python scripts/smoke_writeback.py [PR_URL]
会真实调用 DeepSeek + 写真实 GitHub PR 评论（只写 tljcpa 自己的 demo PR）。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.github_fetcher import GitHubFetcher  # noqa: E402
from app.services.pr_writeback import PRWritebackService  # noqa: E402
from app.services.review_service import ReviewService  # noqa: E402


def main():
    url = sys.argv[1] if len(sys.argv) > 1 else "https://github.com/tljcpa/qiniu-pr-review/pull/31"
    print(f"=== review + 回写: {url} ===")

    out = ReviewService().review_pr(url, use_cache=True)
    r = out.report
    print(f"findings: {r.total_findings} (high {r.high_count})")
    for f in r.findings:
        print(f"  - {f.severity.value} {f.file}:{f.line_hint} {f.title}")

    print("\n--- 写回 PR ---")
    pr = GitHubFetcher().fetch(url)
    res = PRWritebackService().write_back(url, pr, r)
    print(f"ok={res.ok}")
    print(f"inline={res.inline_count} summary_only={res.summary_only_count}")
    print(f"review_url={res.review_url}")
    print("\n[OK]")


if __name__ == "__main__":
    main()
