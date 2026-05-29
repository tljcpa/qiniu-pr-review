import { describe, expect, it } from "vitest";
import { renderLine, splitFindings } from "./logic";
import type { Finding, ProgressEvent } from "./types";

function ev(type: string, data: Record<string, unknown> = {}): ProgressEvent {
  return { type, data, ts: 0 };
}

describe("renderLine", () => {
  it("connected", () => {
    expect(renderLine(ev("connected")).label).toBe("connect");
  });

  it("fetch_done 拼接统计", () => {
    const l = renderLine(ev("fetch_done", { title: "修复登录", additions: 6, deletions: 1, changed_files: 1 }));
    expect(l.label).toBe("fetch");
    expect(l.text).toContain("修复登录");
    expect(l.text).toContain("+6 -1");
    expect(l.text).toContain("1 files");
  });

  it("context_built 含截断提示", () => {
    const withT = renderLine(ev("context_built", { level: "L4", tokens: 5000, truncated: 2 }));
    expect(withT.text).toContain("L4");
    expect(withT.text).toContain("2 truncated");
    const noT = renderLine(ev("context_built", { level: "L2", tokens: 100, truncated: 0 }));
    expect(noT.text).not.toContain("truncated");
  });

  it("deep_read_start 索引从 1 显示", () => {
    const l = renderLine(ev("deep_read_start", { index: 0, total: 3, title: "空指针" }));
    expect(l.text).toContain("[1/3]");
    expect(l.text).toContain("空指针");
  });

  it("finding_verdict 确认与误报不同色", () => {
    const ok = renderLine(ev("finding_verdict", { dropped: false, verdict: "confirmed", title: "SQL 注入" }));
    expect(ok.text).toContain("confirmed");
    expect(ok.tone).toBe("text-fg");
    const dropped = renderLine(ev("finding_verdict", { dropped: true, title: "误报项" }));
    expect(dropped.text).toContain("false positive");
    expect(dropped.tone).toBe("text-sev_med");
  });

  it("cache_hit / done 用强调色", () => {
    expect(renderLine(ev("cache_hit")).tone).toBe("text-amber");
    expect(renderLine(ev("done")).tone).toBe("text-amber");
  });

  it("error 用危险色并展示消息", () => {
    const l = renderLine(ev("error", { message: "找不到 PR" }));
    expect(l.tone).toBe("text-sev_high");
    expect(l.text).toContain("找不到 PR");
  });

  it("未知事件兜底", () => {
    const l = renderLine(ev("weird", { a: 1 }));
    expect(l.label).toBe("weird");
    expect(l.text).toContain("a");
  });
});

describe("splitFindings", () => {
  const mk = (confidence: string, title: string): Finding =>
    ({
      file: "x.py", line_hint: "", severity: "high", category: "bug",
      title, detail: "", suggestion: "", confidence: confidence as Finding["confidence"],
      confidence_score: 0.5, verdict: "confirmed", deep_read: true,
      reasoning: null, cross_check: "none", cross_note: null,
    });

  it("低置信进折叠区，其余进主区", () => {
    const { mainFindings, lowFindings } = splitFindings([
      mk("high", "A"), mk("low", "B"), mk("medium", "C"), mk("low", "D"),
    ]);
    expect(mainFindings.map((f) => f.title)).toEqual(["A", "C"]);
    expect(lowFindings.map((f) => f.title)).toEqual(["B", "D"]);
  });

  it("空数组安全", () => {
    const { mainFindings, lowFindings } = splitFindings([]);
    expect(mainFindings).toEqual([]);
    expect(lowFindings).toEqual([]);
  });
});
