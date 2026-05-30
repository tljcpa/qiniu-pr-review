"""PR 回写服务测试（stub httpx client，不打网络）。"""

import json

import pytest

from app.models.finding import Finding, ReviewReport
from app.services.github_fetcher import ChangedFile, PullRequestData
from app.services.pr_writeback import (
    PRWritebackError,
    PRWritebackService,
    _new_side_lines,
    _parse_line_number,
)


# ---------- 纯函数 ----------

def test_parse_line_number():
    assert _parse_line_number("L8") == 8
    assert _parse_line_number("line 12") == 12
    assert _parse_line_number("第 5 行附近") == 5
    assert _parse_line_number("find_account 函数") is None
    assert _parse_line_number("") is None


def test_new_side_lines_maps_added_and_context():
    # @@ -1,2 +10,3 @@ 新文件从第 10 行起：context(10) + added(11) + context(12)
    patch = "@@ -1,2 +10,3 @@\n ctx\n+added\n more\n"
    lines = _new_side_lines(patch)
    assert lines == {10, 11, 12}


def test_new_side_lines_skips_deletions():
    # 删除行不占新文件行号
    patch = "@@ -1,3 +5,2 @@\n keep\n-removed\n+new\n"
    lines = _new_side_lines(patch)
    # keep=5, new=6（removed 跳过，不推进新文件行号）
    assert lines == {5, 6}


def test_new_side_lines_none_patch():
    assert _new_side_lines(None) == set()


# ---------- 构造数据 ----------

def _pr(patch="@@ -1,2 +10,3 @@\n ctx\n+risky line\n more\n"):
    f = ChangedFile(filename="app/pay.py", status="modified",
                    additions=1, deletions=0, changes=1, patch=patch)
    return PullRequestData(
        owner="tljcpa", repo="qiniu-pr-review", number=31, title="t", body="",
        author="dev", state="open", base_ref="main", head_ref="f",
        base_sha="b", head_sha="HEADSHA",
        additions=1, deletions=0, changed_files_count=1, commits=1,
        html_url="https://github.com/tljcpa/qiniu-pr-review/pull/31", files=[f],
    )


def _finding(file="app/pay.py", line_hint="L11", title="SQL 注入", severity="high"):
    return Finding(
        file=file, line_hint=line_hint, severity=severity, category="security",
        title=title, detail="拼接 SQL", suggestion="参数化",
        confidence="high", confidence_score=0.95, verdict="confirmed",
        deep_read=True, reasoning="推理" * 50, cross_check="agree",
    )


def _report(findings):
    high = sum(1 for f in findings if f.severity.value == "high")
    return ReviewReport(summary="改了支付", context_level="L2", findings=findings,
                        total_findings=len(findings), high_count=high)


class _StubResp:
    def __init__(self, status_code, data=None, text=""):
        self.status_code = status_code
        self._data = data or {}
        self.text = text

    def json(self):
        return self._data


class _StubClient:
    """记录最后一次 POST 的 payload，按预设返回。"""

    def __init__(self, responses):
        self._responses = list(responses)
        self.posts = []

    def post(self, url, json=None, headers=None):
        self.posts.append({"url": url, "json": json})
        return self._responses.pop(0)


# ---------- 写回行为 ----------

def test_writeback_inline_for_diff_line():
    # line_hint=L11 落在 diff（新文件 10/11/12）-> 挂 inline
    svc_client = _StubClient([_StubResp(200, {"html_url": "https://github.com/tljcpa/qiniu-pr-review/pull/31#review"})])
    svc = PRWritebackService(token="x", client=svc_client)
    report = _report([_finding(line_hint="L11")])
    res = svc.write_back("https://github.com/tljcpa/qiniu-pr-review/pull/31", _pr(), report)
    assert res.ok
    assert res.inline_count == 1
    payload = svc_client.posts[0]["json"]
    assert payload["event"] == "COMMENT"
    assert payload["commit_id"] == "HEADSHA"
    assert payload["comments"][0]["path"] == "app/pay.py"
    assert payload["comments"][0]["line"] == 11
    assert payload["comments"][0]["side"] == "RIGHT"


def test_writeback_offdiff_finding_goes_to_summary():
    # line_hint=L99 不在 diff -> 不挂 inline，归入 summary
    svc_client = _StubClient([_StubResp(201, {"html_url": "u"})])
    svc = PRWritebackService(token="x", client=svc_client)
    report = _report([_finding(line_hint="L99")])
    res = svc.write_back("https://github.com/tljcpa/qiniu-pr-review/pull/31", _pr(), report)
    assert res.inline_count == 0
    assert res.summary_only_count == 1
    payload = svc_client.posts[0]["json"]
    assert "comments" not in payload  # 无 inline


def test_writeback_safety_guard_rejects_other_owner():
    svc = PRWritebackService(token="x", client=_StubClient([]))
    pr = _pr()
    pr.owner = "someoneelse"
    report = _report([_finding()])
    with pytest.raises(PRWritebackError):
        svc.write_back("https://github.com/someoneelse/repo/pull/1", pr, report)


def test_writeback_422_falls_back_to_summary():
    # 第一次带 inline 422，降级重试纯 summary 成功
    svc_client = _StubClient([
        _StubResp(422, text="line not in diff"),
        _StubResp(200, {"html_url": "u"}),
    ])
    svc = PRWritebackService(token="x", client=svc_client)
    report = _report([_finding(line_hint="L11")])
    res = svc.write_back("https://github.com/tljcpa/qiniu-pr-review/pull/31", _pr(), report)
    assert res.ok
    assert res.inline_count == 0  # 降级后无 inline
    assert len(svc_client.posts) == 2
    assert "comments" not in svc_client.posts[1]["json"]


def test_writeback_no_token_raises():
    svc = PRWritebackService(token="", client=_StubClient([]))
    with pytest.raises(PRWritebackError):
        svc.write_back("https://github.com/tljcpa/qiniu-pr-review/pull/31", _pr(), _report([_finding()]))


def test_writeback_http_error_raises():
    svc_client = _StubClient([_StubResp(403, text="forbidden")])
    svc = PRWritebackService(token="x", client=svc_client)
    with pytest.raises(PRWritebackError):
        svc.write_back("https://github.com/tljcpa/qiniu-pr-review/pull/31", _pr(), _report([_finding(line_hint="L99")]))
