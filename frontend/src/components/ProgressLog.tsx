import type { ProgressEvent } from "../types";

// 把一个 SSE 事件渲染成一行控制台日志
function renderLine(ev: ProgressEvent): { label: string; text: string; tone: string } {
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

function clock(ts: number): string {
  const dt = new Date(ts);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(dt.getHours())}:${pad(dt.getMinutes())}:${pad(dt.getSeconds())}`;
}

interface Props {
  events: ProgressEvent[];
  running: boolean;
}

export function ProgressLog({ events, running }: Props) {
  if (events.length === 0 && !running) {
    return null;
  }
  return (
    <div className="border border-line bg-panel">
      <div className="flex items-center gap-2 border-b border-line px-3 py-1.5 text-faint">
        <span className="text-amber">●</span>
        <span className="text-xs uppercase tracking-wider">pipeline</span>
      </div>
      <div className="px-3 py-2">
        {events.map((ev, i) => {
          const { label, text, tone } = renderLine(ev);
          return (
            <div key={i} className="flex items-baseline gap-3 py-0.5">
              <span className="shrink-0 text-faint">{clock(ev.ts)}</span>
              <span className="w-16 shrink-0 text-amberdim">{label}</span>
              <span className={`break-all ${tone}`}>{text}</span>
            </div>
          );
        })}
        {running && (
          <div className="flex items-baseline gap-3 py-0.5">
            <span className="shrink-0 text-faint">{clock(Date.now())}</span>
            <span className="w-16 shrink-0 text-amberdim">···</span>
            <span className="text-amber">
              working<span className="animate-blink">_</span>
            </span>
          </div>
        )}
      </div>
    </div>
  );
}
