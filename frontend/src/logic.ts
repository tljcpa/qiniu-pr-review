// 纯逻辑（无 React/DOM 依赖），便于单测。
// 从 ProgressLog / App 中抽出 SSE 事件渲染与 finding 分组逻辑（见复盘 D-30）。
import type { Finding, ProgressEvent } from "./types";

export interface LogLine {
  label: string;
  text: string;
  tone: string;
}

// 把一个 SSE 事件渲染成一行控制台日志（行为与原 ProgressLog.renderLine 一致）
export function renderLine(ev: ProgressEvent): LogLine {
  const d = ev.data;
  switch (ev.type) {
    case "connected":
      return { label: "connect", text: "connected to review service", tone: "text-faint" };
    case "fetch_start":
      return { label: "fetch", text: `GET ${String(d.url ?? "")}`, tone: "text-muted" };
    case "fetch_done":
      return {
        label: "fetch",
        text: `${String(d.title ?? "")}  +${d.additions ?? 0} -${d.deletions ?? 0}  ${d.changed_files ?? 0} files`,
        tone: "text-fg",
      };
    case "context_built":
      return {
        label: "context",
        text: `layer ${String(d.level ?? "")}  ~${d.tokens ?? 0} tok${Number(d.truncated) > 0 ? `  ${d.truncated} truncated` : ""}`,
        tone: "text-fg",
      };
    case "cache_hit":
      return { label: "cache", text: "hit — returning cached report", tone: "text-amber" };
    case "scan_start":
      return { label: "scan", text: "deepseek-chat scanning…", tone: "text-muted" };
    case "scan_done":
      return {
        label: "scan",
        text: `done — ${d.candidate_count ?? 0} candidate(s)`,
        tone: "text-fg",
      };
    case "deep_read_start":
      return {
        label: "reason",
        text: `deepseek-reasoner [${Number(d.index) + 1}/${d.total}] ${String(d.title ?? "")}`,
        tone: "text-muted",
      };
    case "finding_verdict": {
      const dropped = Boolean(d.dropped);
      return {
        label: "verdict",
        text: dropped
          ? `false positive — dropped: ${String(d.title ?? "")}`
          : `${String(d.verdict ?? "")} — ${String(d.title ?? "")}`,
        tone: dropped ? "text-sev_med" : "text-fg",
      };
    }
    case "review_done":
      return { label: "review", text: `complete — ${d.finding_count ?? 0} kept`, tone: "text-fg" };
    case "done":
      return { label: "done", text: "finished", tone: "text-amber" };
    case "error":
      return { label: "error", text: String(d.message ?? "error"), tone: "text-sev_high" };
    default:
      return { label: ev.type, text: JSON.stringify(d), tone: "text-faint" };
  }
}

// 把 findings 按是否低置信拆开：低置信默认折叠（误报控制 UI 体现）
export function splitFindings(findings: Finding[]): {
  mainFindings: Finding[];
  lowFindings: Finding[];
} {
  const main: Finding[] = [];
  const low: Finding[] = [];
  for (const f of findings) {
    if (f.confidence === "low") {
      low.push(f);
    } else {
      main.push(f);
    }
  }
  return { mainFindings: main, lowFindings: low };
}
