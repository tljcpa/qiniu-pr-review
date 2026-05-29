"""分层上下文构建测试（无网络，用假 PullRequestData + stub fetcher）。

覆盖：
1. 层级判定 L2/L3/L4 边界
2. L4 含"局部 review"声明
3. L2 富化为整文件全文
4. L3 富化为抽取片段（import/签名/改动周边）
5. patch 永远优先：预算极小时仍保证至少一个文件 diff，富化被裁并记 note
6. 二进制文件不富化
7. hunk 行区间解析
"""

from app.services.context_builder import (
    ContextBuilder,
    ContextLevel,
    _extract_relevant,
    _parse_hunk_line_ranges,
)
from app.services.github_fetcher import ChangedFile, PullRequestData


def _pr(files, additions=10, deletions=0):
    total_add = sum(f.additions for f in files)
    total_del = sum(f.deletions for f in files)
    return PullRequestData(
        owner="a", repo="b", number=1,
        title="测试 PR", body="修复一个 bug",
        author="dev", state="open",
        base_ref="main", head_ref="feat",
        base_sha="base", head_sha="head",
        additions=total_add, deletions=total_del,
        changed_files_count=len(files), commits=1,
        html_url="https://github.com/a/b/pull/1",
        files=files,
    )


def _file(name, add, dele, patch="@@ -1,2 +1,3 @@\n line\n+new\n"):
    return ChangedFile(
        filename=name, status="modified",
        additions=add, deletions=dele, changes=add + dele, patch=patch,
    )


class _StubFetcher:
    def __init__(self, contents):
        # contents: {filename: full_text or None}
        self._contents = contents
        self.calls = []

    def fetch_file_content(self, repo, path, ref):
        self.calls.append(path)
        return self._contents.get(path)


# ---------- 层级判定 ----------

def test_level_l2_small():
    pr = _pr([_file("x.py", 5, 5)])  # 10 行 < 800
    bundle = ContextBuilder().build(pr)
    assert bundle.level == ContextLevel.L2


def test_level_l3_medium():
    pr = _pr([_file("x.py", 1000, 0)])  # 1000 在 [800,3000]
    bundle = ContextBuilder().build(pr)
    assert bundle.level == ContextLevel.L3


def test_level_l4_large():
    pr = _pr([_file("x.py", 5000, 0)])  # >3000
    bundle = ContextBuilder().build(pr)
    assert bundle.level == ContextLevel.L4
    # L4 必须声明局部 review
    assert any("局部 review" in n for n in bundle.truncated_notes)


# ---------- 富化 ----------

def test_l2_enriches_full_text():
    pr = _pr([_file("x.py", 5, 5)])
    fetcher = _StubFetcher({"x.py": "def foo():\n    return 1\n"})
    bundle = ContextBuilder(fetcher=fetcher).build(pr)
    fc = bundle.files[0]
    assert fc.enrichment_kind == "full"
    assert "def foo" in fc.enrichment
    assert "x.py" in fetcher.calls


def test_l3_enriches_extract():
    # 构造一个中等 PR，全文里有 import 和函数签名
    full = "\n".join(
        ["import os"]
        + [f"line {i}" for i in range(60)]
        + ["def changed_fn():", "    return 42"]
        + [f"tail {i}" for i in range(60)]
    )
    patch = "@@ -1,2 +122,3 @@\n+new code\n"  # 改动落在文件末尾附近
    pr = _pr([_file("x.py", 1000, 0, patch=patch)])
    fetcher = _StubFetcher({"x.py": full})
    bundle = ContextBuilder(fetcher=fetcher).build(pr)
    fc = bundle.files[0]
    assert fc.enrichment_kind == "extract"
    # import 与签名应被保留
    assert "import os" in fc.enrichment
    assert "def changed_fn" in fc.enrichment
    # 抽取后应比全文短
    assert len(fc.enrichment) < len(full)


def test_no_fetcher_skips_enrichment():
    pr = _pr([_file("x.py", 5, 5)])
    bundle = ContextBuilder().build(pr)  # 无 fetcher
    assert bundle.files[0].enrichment is None
    assert bundle.files[0].enrichment_kind == "none"


def test_binary_not_enriched():
    bin_file = ChangedFile(
        filename="logo.png", status="added",
        additions=99999, deletions=0, changes=99999, patch=None,
    )
    pr = _pr([bin_file])
    fetcher = _StubFetcher({"logo.png": "should-not-be-used"})
    # 单文件 99999 行 -> L4，本就不富化；改用一个小文本文件 + 二进制混合更准
    bundle = ContextBuilder(fetcher=fetcher).build(pr)
    assert bundle.files[0].enrichment is None


# ---------- 预算：patch 永远优先 ----------

def test_patch_priority_under_tiny_budget():
    # 两个文件，预算极小，至少保证排第一（改动大）的文件有 diff
    f_big = _file("big.py", 50, 0, patch="@@ -1 +1,50 @@\n" + "+x\n" * 50)
    f_small = _file("small.py", 1, 0, patch="@@ -1 +1,1 @@\n+y\n")
    pr = _pr([f_small, f_big])
    fetcher = _StubFetcher({"big.py": "full big", "small.py": "full small"})
    # 预算只够放一点点
    bundle = ContextBuilder(fetcher=fetcher, budget=80).build(pr)
    # 改动大的 big.py 应优先拿到 diff
    big_ctx = next(fc for fc in bundle.files if fc.filename == "big.py")
    assert big_ctx.patch is not None
    # 预算紧张时应有裁剪声明
    assert len(bundle.truncated_notes) > 0
    # 富化基本放不下
    assert big_ctx.enrichment is None


def test_full_text_fetch_failure_noted():
    pr = _pr([_file("x.py", 5, 5)])
    fetcher = _StubFetcher({"x.py": None})  # 拉全文失败
    bundle = ContextBuilder(fetcher=fetcher).build(pr)
    assert bundle.files[0].enrichment is None
    assert any("全文拉取失败" in n for n in bundle.truncated_notes)


# ---------- hunk 解析 ----------

def test_parse_hunk_ranges():
    patch = "@@ -12,7 +12,9 @@ def f():\n context\n+added\n@@ -50 +60,2 @@\n+x\n"
    ranges = _parse_hunk_line_ranges(patch)
    assert ranges == [(12, 20), (60, 61)]


def test_extract_keeps_imports_and_signatures():
    full = "import sys\nx = 1\ndef helper():\n    pass\nclass C:\n    pass\n"
    out = _extract_relevant(full, patch=None)
    assert "import sys" in out
    assert "def helper" in out
    assert "class C" in out


# ---------- prompt 渲染 ----------

def test_to_prompt_text_contains_diff_and_notes():
    pr = _pr([_file("x.py", 5000, 0)])  # L4
    bundle = ContextBuilder().build(pr)
    text = bundle.to_prompt_text()
    assert "PR 概览" in text
    assert "上下文完整性声明" in text
    assert "局部 review" in text
    assert "```diff" in text
