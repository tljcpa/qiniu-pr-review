"""GitHub Fetcher 单元测试（stub 注入，不打真实网络）。

覆盖：
1. URL / 简写解析（含 /files 后缀、.git 后缀、非法输入）
2. 正常拉取的字段映射
3. 大 PR 文件数截断（files_truncated）
4. patch 为 None（二进制/超大）时的 is_binary_or_too_large
5. fetch_file_content 的降级（目录 / 解码失败 -> None）
"""

import types

import pytest

from app.services.github_fetcher import (
    GitHubFetcher,
    GitHubFetchError,
    _friendly_github_error,
    parse_pr_url,
)


def test_friendly_error_404_no_raw_json():
    msg = _friendly_github_error(404)
    assert "找不到" in msg
    # 不暴露原始 GitHub JSON
    assert "documentation_url" not in msg
    assert "{" not in msg


def test_friendly_error_known_codes():
    assert "限流" in _friendly_github_error(403)
    assert "鉴权" in _friendly_github_error(401)
    assert "格式" in _friendly_github_error(422)


def test_friendly_error_unknown_code():
    assert "HTTP 500" in _friendly_github_error(500)


# ---------- URL 解析 ----------

def test_parse_full_url():
    assert parse_pr_url("https://github.com/psf/requests/pull/6432") == ("psf", "requests", 6432)


def test_parse_url_with_files_suffix():
    got = parse_pr_url("https://github.com/psf/requests/pull/6432/files")
    assert got == ("psf", "requests", 6432)


def test_parse_url_with_git_suffix_is_tolerated():
    # repo 名误带 .git 时应被剥掉
    got = parse_pr_url("https://github.com/a/b.git/pull/7")
    assert got == ("a", "b", 7)


def test_parse_short_form():
    assert parse_pr_url("psf/requests#6432") == ("psf", "requests", 6432)


def test_parse_invalid_raises():
    with pytest.raises(GitHubFetchError):
        parse_pr_url("not a pr url")


def test_parse_rejects_non_github_host():
    # SSRF 收口（D-33）：只接受 github.com，拒绝其他域名/绕过写法
    bad = [
        "https://evil.com/x/y/pull/1",
        "https://evil.com/github.com/x/y/pull/1",  # 把 github.com 塞进路径绕过
        "https://github.com.evil.com/x/y/pull/1",  # 域名后缀伪装
        "http://169.254.169.254/x/y/pull/1",       # 云元数据 IP
        "ftp://github.com/x/y/pull/1",             # 非 http(s) 协议
        "https://notgithub.com/x/y/pull/1",
    ]
    for url in bad:
        with pytest.raises(GitHubFetchError):
            parse_pr_url(url)


def test_parse_accepts_www_github():
    # www.github.com 是合法的
    assert parse_pr_url("https://www.github.com/psf/requests/pull/6432") == (
        "psf", "requests", 6432,
    )


# ---------- stub 构造 ----------

def _gh_file(filename, status="modified", add=1, dele=1, patch="@@ -1 +1 @@", sha="abc"):
    changes = add + dele
    return types.SimpleNamespace(
        filename=filename,
        status=status,
        additions=add,
        deletions=dele,
        changes=changes,
        patch=patch,
        previous_filename=None,
        sha=sha,
    )


def _stub_pr(files):
    base = types.SimpleNamespace(ref="main", sha="basesha")
    head = types.SimpleNamespace(ref="feature", sha="headsha")
    user = types.SimpleNamespace(login="octocat")
    return types.SimpleNamespace(
        title="修复登录空指针",
        body="本 PR 修复了 X",
        user=user,
        state="open",
        base=base,
        head=head,
        additions=10,
        deletions=3,
        changed_files=len(files),
        commits=2,
        html_url="https://github.com/a/b/pull/1",
        get_files=lambda: files,
    )


class _StubRepo:
    def __init__(self, pr, contents=None):
        self._pr = pr
        self._contents = contents

    def get_pull(self, number):
        return self._pr

    def get_contents(self, path, ref=None):
        if self._contents is None:
            raise RuntimeError("no content configured")
        return self._contents


class _StubClient:
    def __init__(self, repo):
        self._repo = repo

    def get_repo(self, full_name):
        return self._repo


class _RaisingClient:
    """get_repo 抛 GithubException(status)，验证 fetch 把它翻译成友好消息。"""

    def __init__(self, status):
        self._status = status

    def get_repo(self, full_name):
        from github.GithubException import GithubException
        raise GithubException(self._status, {"message": "Not Found"}, None)


def test_fetch_404_surfaces_friendly_message():
    # 回归 L-07：404 应是友好中文，且不含原始 JSON（这条能抓住"helper 没接上"的回归）
    fetcher = GitHubFetcher(client=_RaisingClient(404))
    with pytest.raises(GitHubFetchError) as ei:
        fetcher.fetch("https://github.com/a/b/pull/1")
    msg = str(ei.value)
    assert "找不到" in msg
    assert "documentation_url" not in msg
    assert "{" not in msg


# ---------- fetch ----------

def test_fetch_maps_fields():
    files = [_gh_file("app/login.py"), _gh_file("README.md", status="added", add=5, dele=0)]
    client = _StubClient(_StubRepo(_stub_pr(files)))
    fetcher = GitHubFetcher(client=client)
    data = fetcher.fetch("https://github.com/a/b/pull/1")

    assert data.title == "修复登录空指针"
    assert data.author == "octocat"
    assert data.base_ref == "main"
    assert data.head_sha == "headsha"
    assert data.total_changed_lines == 13
    assert len(data.files) == 2
    assert data.files_truncated is False
    assert data.files[0].filename == "app/login.py"


def test_fetch_truncates_large_pr():
    files = [_gh_file(f"f{i}.py") for i in range(10)]
    client = _StubClient(_StubRepo(_stub_pr(files)))
    fetcher = GitHubFetcher(client=client, max_files=4)
    data = fetcher.fetch("a/b#1")
    assert len(data.files) == 4
    assert data.files_truncated is True


def test_binary_file_patch_none():
    files = [_gh_file("logo.png", status="added", add=0, dele=0, patch=None)]
    # 二进制文件 changes 通常为 0；构造一个 changes>0 但 patch None 的超大文件场景
    files.append(_gh_file("huge.min.js", add=99999, dele=0, patch=None))
    client = _StubClient(_StubRepo(_stub_pr(files)))
    fetcher = GitHubFetcher(client=client)
    data = fetcher.fetch("a/b#1")
    assert data.files[0].is_binary_or_too_large is False  # changes=0
    assert data.files[1].is_binary_or_too_large is True   # changes>0 且 patch None


def test_author_deleted_user():
    pr = _stub_pr([_gh_file("x.py")])
    pr.user = None
    client = _StubClient(_StubRepo(pr))
    data = GitHubFetcher(client=client).fetch("a/b#1")
    assert data.author == "unknown"


# ---------- fetch_file_content 降级 ----------

def test_file_content_directory_returns_none():
    # get_contents 返回 list 表示目录，应降级 None
    repo = _StubRepo(_stub_pr([]), contents=[object(), object()])
    data = GitHubFetcher(client=_StubClient(repo)).fetch_file_content("a/b", "src", "headsha")
    assert data is None


def test_file_content_decode_ok():
    content_file = types.SimpleNamespace(decoded_content="def f():\n    return 1\n".encode("utf-8"))
    repo = _StubRepo(_stub_pr([]), contents=content_file)
    data = GitHubFetcher(client=_StubClient(repo)).fetch_file_content("a/b", "f.py", "headsha")
    assert data == "def f():\n    return 1\n"


def test_file_content_decode_failure_returns_none():
    # decoded_content 抛异常（模拟 >1MB）
    class _Boom:
        @property
        def decoded_content(self):
            raise RuntimeError(">1MB")
    repo = _StubRepo(_stub_pr([]), contents=_Boom())
    data = GitHubFetcher(client=_StubClient(repo)).fetch_file_content("a/b", "big.py", "headsha")
    assert data is None
