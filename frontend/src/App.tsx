import { useMemo, useState } from "react";
import { FindingCard } from "./components/FindingCard";
import { ProgressLog } from "./components/ProgressLog";
import { useReview } from "./useReview";
import type { Finding } from "./types";

const SAMPLE = "https://github.com/psf/requests/pull/7487";

export default function App() {
  const [url, setUrl] = useState("");
  const [useCache, setUseCache] = useState(true);
  const [showLow, setShowLow] = useState(false);
  const { state, start, reset } = useReview();

  const running = state.status === "running";

  // 低置信默认折叠（误报控制 UI 体现）
  const { mainFindings, lowFindings } = useMemo(() => {
    const findings = state.report?.findings ?? [];
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
  }, [state.report]);

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const target = url.trim();
    if (!target || running) {
      return;
    }
    void start(target, useCache);
  };

  return (
    <div className="min-h-full">
      {/* 顶栏：工具风，左标识右元信息 */}
      <header className="border-b border-line bg-panel">
        <div className="mx-auto flex max-w-5xl items-baseline gap-3 px-4 py-2.5">
          <span className="font-bold text-amber">pr-review</span>
          <span className="text-faint">/</span>
          <span className="text-muted">GitHub PR 代码评审</span>
          <span className="ml-auto text-xs text-faint">
            deepseek-chat + reasoner · 分层上下文 · 思维链可见
          </span>
        </div>
      </header>

      <main className="mx-auto max-w-5xl px-4 py-5">
        {/* 输入区：单行命令式 */}
        <form onSubmit={onSubmit} className="border border-line bg-panel">
          <div className="flex items-center gap-2 border-b border-line px-3 py-2">
            <span className="text-amberdim">pr</span>
            <input
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="github.com/owner/repo/pull/123"
              className="w-full bg-transparent text-fg placeholder:text-faint focus:outline-none"
              disabled={running}
              spellCheck={false}
            />
            <button
              type="submit"
              disabled={running || !url.trim()}
              className="shrink-0 border border-amberdim bg-panel2 px-4 py-1 font-bold text-amber transition-colors hover:bg-amberdim hover:text-bg disabled:cursor-not-allowed disabled:opacity-40"
            >
              {running ? "reviewing…" : "review"}
            </button>
          </div>
          <div className="flex flex-wrap items-center gap-4 px-3 py-1.5 text-xs text-muted">
            <label className="flex cursor-pointer items-center gap-1.5">
              <input
                type="checkbox"
                checked={useCache}
                onChange={(e) => setUseCache(e.target.checked)}
                className="accent-amber"
              />
              use cache
            </label>
            <button
              type="button"
              onClick={() => setUrl(SAMPLE)}
              className="text-link hover:text-fg"
              disabled={running}
            >
              load sample
            </button>
            {state.status !== "idle" && (
              <button type="button" onClick={reset} className="text-faint hover:text-fg">
                clear
              </button>
            )}
          </div>
        </form>

        {/* 进度日志流 */}
        <div className="mt-4">
          <ProgressLog events={state.events} running={running} />
        </div>

        {/* 错误 */}
        {state.status === "error" && (
          <div className="mt-4 border border-sev_high border-l-2 bg-panel px-3 py-2 text-sev_high">
            error: {state.error}
          </div>
        )}

        {/* 结果 */}
        {state.report && (
          <div className="mt-5">
            {/* 总结 + 统计条 */}
            <div className="border border-line bg-panel">
              <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1 border-b border-line px-3 py-1.5 text-xs">
                <span className="text-muted">
                  context <span className="text-fg">{state.report.context_level}</span>
                </span>
                <span className="text-muted">
                  high <span className="font-bold text-sev_high">{state.report.high_count}</span>
                </span>
                <span className="text-muted">
                  med <span className="font-bold text-sev_med">{state.report.medium_count}</span>
                </span>
                <span className="text-muted">
                  low <span className="font-bold text-sev_low">{state.report.low_count}</span>
                </span>
                {state.meta?.from_cache && <span className="text-amber">[cache hit]</span>}
                {typeof state.report.usage.total_tokens === "number" && (
                  <span className="ml-auto text-faint">
                    {state.report.usage.total_tokens} tok
                  </span>
                )}
              </div>
              <div className="px-3 py-2">
                <div className="mb-1 text-xs uppercase tracking-wider text-amber">summary</div>
                <p className="whitespace-pre-wrap text-fg">{state.report.summary}</p>
              </div>
            </div>

            {/* findings */}
            {mainFindings.length === 0 && lowFindings.length === 0 ? (
              <div className="mt-3 border border-amberdim border-l-2 bg-panel px-3 py-2 text-fg">
                no significant risk — 候选问题经 reasoner 二次核实后未保留误报。
              </div>
            ) : (
              <div className="mt-3 space-y-2">
                {mainFindings.map((f, i) => (
                  <FindingCard key={`m-${i}`} finding={f} />
                ))}
              </div>
            )}

            {/* 低置信折叠 */}
            {lowFindings.length > 0 && (
              <div className="mt-3">
                <button
                  onClick={() => setShowLow((v) => !v)}
                  className="w-full border border-line bg-panel px-3 py-1.5 text-left text-xs text-muted hover:text-fg"
                >
                  {showLow ? "[-]" : "[+]"} {lowFindings.length} low-confidence finding(s) — folded (possible false positives)
                </button>
                {showLow && (
                  <div className="mt-2 space-y-2">
                    {lowFindings.map((f, i) => (
                      <FindingCard key={`l-${i}`} finding={f} />
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        <footer className="mt-10 border-t border-line pt-3 text-xs text-faint">
          七牛云 × XEngineer 题目三 · 模型路由 / 分层上下文 / 思维链可见 / 误报控制
        </footer>
      </main>
    </div>
  );
}
