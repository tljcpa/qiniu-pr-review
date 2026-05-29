import type { ProgressEvent } from "../types";
import { renderLine } from "../logic";

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
