"""交叉验证端到端实测（真实调用 DeepSeek + Azure）。

用法：cd backend && ../.venv/bin/python scripts/smoke_cross.py [PR_URL]
打印每条 finding 的 verdict / severity / cross_check / confidence，重点看高风险项是否被 Azure 二次验证。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.review_service import ReviewService  # noqa: E402


def main():
    url = sys.argv[1] if len(sys.argv) > 1 else "https://github.com/psf/requests/pull/7487"
    print(f"=== 交叉验证实测: {url} ===")
    svc = ReviewService(enable_cross_validate=True)
    out = svc.review_pr(url, use_cache=False)
    r = out.report
    print(f"层级 {r.context_level} | findings {r.total_findings} | tok {r.usage.get('total_tokens')}")
    for i, f in enumerate(r.findings, 1):
        print(f"\n[{i}] {f.severity}/{f.category} {f.title}")
        print(f"    verdict={f.verdict} cross_check={f.cross_check} conf={f.confidence}({f.confidence_score})")
        if f.cross_note:
            print(f"    cross_note: {f.cross_note}")
    print("\n--- trace ---")
    for t in r.trace:
        print(f"  · {t}")
    print("\n[OK]")


if __name__ == "__main__":
    main()
