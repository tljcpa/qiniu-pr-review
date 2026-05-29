"""GitHub Fetcher 实测脚本（真实调用 GitHub API，纯网络、低内存，可本机跑）。

用法（仓库根 source 好 .env 或导出 GH_TOKEN 后）：
    cd backend && ../.venv/bin/python scripts/smoke_github.py [PR_URL]

默认拉一个公开小 PR 验证字段、文件清单、单文件全文拉取。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.github_fetcher import GitHubFetcher  # noqa: E402


def main():
    # 默认选一个公开仓库的小 PR
    url = sys.argv[1] if len(sys.argv) > 1 else "https://github.com/psf/requests/pull/6432"
    print(f"拉取 PR: {url}")

    fetcher = GitHubFetcher()
    data = fetcher.fetch(url)

    print(f"\n标题      : {data.title}")
    print(f"作者      : {data.author}  状态: {data.state}")
    print(f"分支      : {data.base_ref} <- {data.head_ref}")
    print(f"统计      : +{data.additions} -{data.deletions}  文件数={data.changed_files_count}  commits={data.commits}")
    print(f"总改动行  : {data.total_changed_lines}  (PR5 据此选 L1-L4 层级)")
    print(f"文件截断  : {data.files_truncated}")
    print(f"\n改动文件 (前 5):")
    for f in data.files[:5]:
        flag = " [二进制/超大]" if f.is_binary_or_too_large else ""
        has_patch = "有patch" if f.patch else "无patch"
        print(f"  {f.status:9} +{f.additions} -{f.deletions}  {f.filename}  ({has_patch}){flag}")

    # 试拉第一个有 patch 的文本文件全文
    target = None
    for f in data.files:
        if f.patch and not f.is_binary_or_too_large:
            target = f
            break
    if target is not None:
        print(f"\n拉取文件全文: {target.filename} @ {data.head_sha[:8]}")
        text = fetcher.fetch_file_content(data.repo_full_name, target.filename, data.head_sha)
        if text is not None:
            print(f"  全文 {len(text)} 字符, 前 3 行:")
            for line in text.splitlines()[:3]:
                print(f"    | {line}")
        else:
            print("  (拿不到全文, 已降级 None)")

    print("\n[OK]")


if __name__ == "__main__":
    main()
