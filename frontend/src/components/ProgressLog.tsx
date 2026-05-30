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

// 审查日志做成"真终端"面板：窗口标题栏 + 等宽日志体 + 提示符感
export function ProgressLog({ events, running }: Props) {
  if (events.length === 0 && !running) {
    return null;
  }
  return (
    <div className="overflow-hidden border border-line bg-bg">
      {/* 终端标题栏：窗口控制点（克制、同主题色，不用红黄绿彩点）+ 路径 */}
      <div className="flex items-center gap-2 border-b border-line bg-panel px-3 py-1.5">
        <span className="flex gap-1.5">
          <span className="h-2 w-2 rounded-full border border-line bg-panel2" />
          <span className="h-2 w-2 rounded-full border border-line bg-panel2" />
          <span className="h-2 w-2 rounded-full border border-amberdim bg-amberdim/30" />
        </span>
        <span className="ml-1 font-mono text-xs text-muted">
          pr-review — pipeline
        </span>
        {running && (
          <span className="ml-auto font-mono text-xs text-amber">
            ● running
          </span>
        )}
      </div>
      {/* 日志体：等宽 + 扫描线质感 */}
      <div className="terminal-grain px-3 py-2 font-mono text-[12.5px] leading-relaxed">
        {events.map((ev, i) => {
          const { label, text, tone } = renderLine(ev);
          return (
            <div key={i} className="flex items-baseline gap-2.5 py-[1px]">
              <span className="shrink-0 select-none text-faint">{clock(ev.ts)}</span>
              <span className="shrink-0 select-none text-amberdim">›</span>
              <span className="w-[4.5rem] shrink-0 text-amberdim">{label}</span>
              <span className={`break-all ${tone}`}>{text}</span>
            </div>
          );
        })}
        {running && (
          <div className="flex items-baseline gap-2.5 py-[1px]">
            <span className="shrink-0 select-none text-faint">{clock(Date.now())}</span>
            <span className="shrink-0 select-none text-amber">$</span>
            <span className="text-amber">
              working<span className="animate-blink">▋</span>
            </span>
          </div>
        )}
      </div>
    </div>
  );
}
