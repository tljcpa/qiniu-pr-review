"""GitHub PR 拉取层。

职责（纯数据获取，不做任何分析）：
1. 解析用户给的 PR 标识（完整 URL 或 owner/repo#123 简写）
2. 拉 PR 元信息（标题/描述/作者/base-head/增删统计）
3. 拉改动文件清单（含每个文件的 patch diff），大 PR 分页并设上限
4. 按需拉某个改动文件在 head 提交处的全文（供 PR5 的 L2/L3 上下文使用）

设计要点（详见复盘 D-12 / D-13）：
- 大 PR 边界：files 列表设 max_files 上限，超出标 files_truncated；patch 可能为 None（二进制/超大文件），保留条目不静默丢。
- 全文拉取单独方法、按需调用：>1MB 文件解码会抛，catch 后返回 None 并标记，不让整次 review 崩。
- 输出用 dataclass（原始事实，无需校验）；Pydantic 留给 PR7 的结构化 Finding。
- client 可注入：单元测试用 stub，不打真实网络。
- 限流：捕获 RateLimitExceededException 做有限退避重试。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from github import Auth, Github
from github.GithubException import GithubException, RateLimitExceededException

from app.config import settings


class GitHubFetchError(Exception):
    """拉取 PR 失败（URL 非法、PR 不存在、鉴权失败、限流耗尽等）。"""


def _friendly_github_error(status: int) -> str:
    """把 GitHub HTTP 状态码翻译成对用户友好的中文提示，不暴露原始 JSON。"""
    if status == 404:
        return "找不到这个 PR：仓库或 PR 编号不存在，或是私有仓库当前 token 无权访问。"
    if status == 403:
        return "GitHub 拒绝访问：可能触发了 API 限流，或 token 无权访问该仓库，请稍后再试。"
    if status == 401:
        return "GitHub 鉴权失败：token 无效或已过期。"
    if status == 422:
        return "请求无法处理：请确认 PR 链接格式正确。"
    return f"GitHub 访问出错（HTTP {status}），请稍后重试或换一个 PR。"


# 匹配 https://github.com/<owner>/<repo>/pull/<number>，允许结尾带 /files、#discussion 等。
# 锚定：必须以 http(s)://[www.]github.com/ 开头（^ 锚定 + 域名后紧跟 /），
# 防止 https://evil.com/github.com/... 这类绕过把请求引向非 github 主机（缩小 SSRF 面，见复盘 D-33）。
_URL_RE = re.compile(
    r"^https?://(?:www\.)?github\.com/(?P<owner>[^/\s]+)/(?P<repo>[^/\s]+)/pull/(?P<number>\d+)"
)
# 匹配简写 owner/repo#123
_SHORT_RE = re.compile(
    r"^(?P<owner>[^/\s]+)/(?P<repo>[^/\s#]+)#(?P<number>\d+)$"
)


@dataclass
class ChangedFile:
    """PR 中一个改动文件。"""

    filename: str
    # added / modified / removed / renamed / copied / changed
    status: str
    additions: int
    deletions: int
    changes: int
    # 统一 diff（unified diff）文本；二进制或超大文件时为 None
    patch: str | None = None
    # 重命名时的旧文件名
    previous_filename: str | None = None
    # blob sha，可用于精确缓存 key
    sha: str | None = None

    @property
    def is_binary_or_too_large(self) -> bool:
        """patch 缺失通常意味着二进制或超大文件，上层应降级处理。"""
        if self.patch is None and self.changes > 0:
            return True
        return False


@dataclass
class PullRequestData:
    """一个 PR 的完整抓取结果。"""

    owner: str
    repo: str
    number: int
    title: str
    body: str
    author: str
    state: str
    base_ref: str
    head_ref: str
    base_sha: str
    head_sha: str
    additions: int
    deletions: int
    changed_files_count: int
    commits: int
    html_url: str
    files: list[ChangedFile] = field(default_factory=list)
    # 改动文件数超过 max_files 时为 True，提醒上层"清单不完整"
    files_truncated: bool = False

    @property
    def repo_full_name(self) -> str:
        return f"{self.owner}/{self.repo}"

    @property
    def total_changed_lines(self) -> int:
        """总改动行数，PR5 据此决定上下文层级 L1-L4。"""
        return self.additions + self.deletions


def parse_pr_url(text: str) -> tuple[str, str, int]:
    """把用户输入解析成 (owner, repo, number)。

    支持两种形式：
    - 完整 URL：https://github.com/owner/repo/pull/123（允许结尾有 /files 等）
    - 简写：owner/repo#123
    解析不出来抛 GitHubFetchError。
    """
    cleaned = text.strip()

    short = _SHORT_RE.match(cleaned)
    if short is not None:
        return short.group("owner"), short.group("repo"), int(short.group("number"))

    found = _URL_RE.search(cleaned)
    if found is not None:
        repo = found.group("repo")
        # 容错：万一 repo 后面粘了 .git
        if repo.endswith(".git"):
            repo = repo[:-4]
        return found.group("owner"), repo, int(found.group("number"))

    raise GitHubFetchError(
        f"无法从输入解析出 PR：{text!r}。"
        f"请用 https://github.com/owner/repo/pull/123 或 owner/repo#123 形式。"
    )


class GitHubFetcher:
    """封装 PyGithub，对外提供 fetch / fetch_file_content 两个方法。"""

    def __init__(
        self,
        token: str | None = None,
        *,
        client: Github | None = None,
        max_files: int = 300,
        max_retries: int = 3,
        base_delay: float = 2.0,
    ) -> None:
        # client 优先（测试注入）；否则按 token 建。无 token 时匿名（限流 60/h）
        if client is not None:
            self._client = client
        else:
            use_token = token if token is not None else settings.gh_token
            if use_token:
                self._client = Github(auth=Auth.Token(use_token))
            else:
                self._client = Github()
        self._max_files = max_files
        self._max_retries = max_retries
        self._base_delay = base_delay

    def _with_retry(self, func, *args, **kwargs):
        """对一次 GitHub 调用做有限退避重试，只重试限流类错误。"""
        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                return func(*args, **kwargs)
            except RateLimitExceededException as exc:
                last_error = exc
                if attempt < self._max_retries:
                    # 限流退避：指数增长，给 secondary rate limit 留足冷却
                    time.sleep(self._base_delay * (2 ** attempt))
                    continue
                break
            except GithubException as exc:
                # 404 / 401 / 422 等：不可重试，翻译成友好中文后抛（不暴露原始 JSON）
                raise GitHubFetchError(_friendly_github_error(exc.status)) from exc
        raise GitHubFetchError(
            f"GitHub 限流重试 {self._max_retries} 次仍失败: {last_error}"
        ) from last_error

    def fetch(self, url_or_ref: str) -> PullRequestData:
        """拉取一个 PR 的元信息 + 改动文件清单。"""
        owner, repo_name, number = parse_pr_url(url_or_ref)

        repo = self._with_retry(self._client.get_repo, f"{owner}/{repo_name}")
        pr = self._with_retry(repo.get_pull, number)

        files, truncated = self._fetch_files(pr)

        return PullRequestData(
            owner=owner,
            repo=repo_name,
            number=number,
            title=pr.title or "",
            body=pr.body or "",
            author=self._safe_login(pr),
            state=pr.state or "",
            base_ref=pr.base.ref,
            head_ref=pr.head.ref,
            base_sha=pr.base.sha,
            head_sha=pr.head.sha,
            additions=pr.additions,
            deletions=pr.deletions,
            changed_files_count=pr.changed_files,
            commits=pr.commits,
            html_url=pr.html_url,
            files=files,
            files_truncated=truncated,
        )

    @staticmethod
    def _safe_login(pr) -> str:
        """作者可能被删号导致 user 为 None，容错。"""
        user = getattr(pr, "user", None)
        if user is not None and getattr(user, "login", None):
            return user.login
        return "unknown"

    def _fetch_files(self, pr) -> tuple[list[ChangedFile], bool]:
        """迭代 PR 改动文件，最多取 max_files 个，超出则标截断。"""
        result: list[ChangedFile] = []
        truncated = False

        # pr.get_files() 是 PaginatedList，迭代时自动翻页；用 enumerate 控上限
        paginated = self._with_retry(pr.get_files)
        for index, gh_file in enumerate(paginated):
            if index >= self._max_files:
                truncated = True
                break
            result.append(
                ChangedFile(
                    filename=gh_file.filename,
                    status=gh_file.status,
                    additions=gh_file.additions,
                    deletions=gh_file.deletions,
                    changes=gh_file.changes,
                    # patch 对二进制/超大文件为 None，原样保留
                    patch=getattr(gh_file, "patch", None),
                    previous_filename=getattr(gh_file, "previous_filename", None),
                    sha=getattr(gh_file, "sha", None),
                )
            )
        return result, truncated

    def fetch_file_content(
        self, repo_full_name: str, path: str, ref: str
    ) -> str | None:
        """拉取某文件在指定 ref（通常是 head_sha）处的全文。

        供 PR5 上下文构建按层级按需调用。
        拿不到（二进制/>1MB/已删除）时返回 None，不抛，让上层降级。
        """
        try:
            repo = self._with_retry(self._client.get_repo, repo_full_name)
            content_file = self._with_retry(repo.get_contents, path, ref=ref)
        except GitHubFetchError:
            return None
        except Exception:
            # get_contents 对超大文件 / 目录会抛各种异常，统一降级
            return None

        # get_contents 对目录返回 list，这里只处理单文件
        if isinstance(content_file, list):
            return None

        try:
            raw = content_file.decoded_content
        except Exception:
            # >1MB 文件 PyGithub 解码会抛
            return None

        if raw is None:
            return None
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            # 非 UTF-8（可能是二进制误判），降级
            return None
