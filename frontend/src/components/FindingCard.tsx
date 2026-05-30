import { useState } from "react";
import type { FixResponse } from "../auth";
import type { Finding } from "../types";

// 严重度只用左边框色条表达（语义，不做装饰性叠色）
const SEV_BORDER: Record<string, string> = {
  high: "border-l-sev_high",
  medium: "border-l-sev_med",
  low: "border-l-sev_low",
};

const SEV_TEXT: Record<string, string> = {
  high: "text-sev_high",
  medium: "text-sev_med",
  low: "text-sev_low",
};

const SEV_LABEL: Record<string, string> = { high: "HIGH", medium: "MED", low: "LOW" };

const CAT_LABEL: Record<string, string> = {
  bug: "bug",
  security: "security",
  logic: "logic",
  maintainability: "maint",
  style: "style",
};

interface Props {
  finding: Finding;
  // onFix 存在时渲染 AI 修复按钮
  onFix?: () => Promise<FixResponse>;
}

export function FindingCard({ finding, onFix }: Props) {
  const [open, setOpen] = useState(false);
  const [fixState, setFixState] = useState<"idle" | "loading" | "done" | "error">("idle");
  const [fixResult, setFixResult] = useState<FixResponse | null>(null);
  const [showPatch, setShowPatch] = useState(false);

  const handleFix = async () => {
    if (!onFix || fixState === "loading") return;
    setFixState("loading");
    setFixResult(null);
    try {
      const result = await onFix();
      setFixResult(result);
      setFixState(result.status === "error" ? "error" : "done");
    } catch (err) {
      setFixResult({ status: "error", patch: null, review_verdict: "", pr_url: null, error: String(err) });
      setFixState("error");
    }
  };

  return (
    <div className={`border border-line border-l-2 bg-panel ${SEV_BORDER[finding.severity] ?? "border-l-sev_low"}`}>
      {/* 头部一行：代码型元信息，全等宽（像 lint 输出的定位行） */}
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 border-b border-line px-3 py-1.5 font-mono text-xs">
        <span className={`font-bold ${SEV_TEXT[finding.severity] ?? "text-sev_low"}`}>
          {SEV_LABEL[finding.severity] ?? finding.severity}
        </span>
        <span className="text-muted">{CAT_LABEL[finding.category] ?? finding.category}</span>
        <span className="text-link">
          {finding.file}
          {finding.line_hint ? `:${finding.line_hint}` : ""}
        </span>
        <span className="ml-auto text-faint">
          conf <span className="text-fg">{finding.confidence}</span> {finding.confidence_score.toFixed(2)}
        </span>
        {finding.verdict === "confirmed" && (
          <span className="text-amber" title="reasoner 深读确认">
            [confirmed]
          </span>
        )}
        {finding.cross_check === "agree" && (
          <span className="text-amber" title="GPT-4.1-mini 交叉验证：同意">
            [x-check ✓]
          </span>
        )}
        {finding.cross_check === "disagree" && (
          <span className="text-sev_med" title="GPT-4.1-mini 交叉验证：有分歧，已降级">
            [x-check ✗ 分歧]
          </span>
        )}
      </div>

      {/* 正文：散文用无衬线（IBM Plex Sans），可读性更好 */}
      <div className="px-3 py-2 font-sans">
        <div className="font-semibold text-fg">{finding.title}</div>

        {finding.detail && (
          <p className="mt-1 whitespace-pre-wrap text-muted">{finding.detail}</p>
        )}

        {finding.suggestion && (
          <div className="mt-2 border-l-2 border-amberdim pl-2 text-fg">
            <span className="font-mono text-amber">fix </span>
            <span className="whitespace-pre-wrap">{finding.suggestion}</span>
          </div>
        )}

        {/* 交叉验证第二意见（亮点 4）：异构模型独立复核结论 */}
        {finding.cross_note && (
          <div className="mt-2 border-l-2 border-line pl-2 text-muted">
            <span className="font-mono text-link">x-check(gpt-4.1-mini) </span>
            <span className="whitespace-pre-wrap">{finding.cross_note}</span>
          </div>
        )}

        {/* 思维链可展开（亮点 3）：触发器与内容都等宽，像查看原始输出 */}
        {finding.reasoning && (
          <div className="mt-2 border-t border-line pt-2">
            <button
              onClick={() => setOpen((v) => !v)}
              className="font-mono text-xs text-link hover:text-fg"
            >
              {open ? "[-]" : "[+]"} reasoning_content (R1 思维链)
            </button>
            {open && (
              <pre className="mt-1 max-h-80 overflow-auto whitespace-pre-wrap border border-line bg-bg p-3 font-mono text-xs leading-relaxed text-muted">
                {finding.reasoning}
              </pre>
            )}
          </div>
        )}

        {/* AI 修复区域（仅登录+绑定 PAT 时渲染 onFix） */}
        {onFix && (
          <div className="mt-2 border-t border-line pt-2">
            {fixState === "idle" && (
              <button
                onClick={() => void handleFix()}
                className="font-mono text-xs border border-amberdim px-2 py-0.5 text-amber hover:bg-amberdim hover:text-bg transition-colors"
              >
                ↑ AI 修复
              </button>
            )}
            {fixState === "loading" && (
              <span className="font-mono text-xs text-muted">
                AI 修复中：生成补丁 → DeepSeek 审核…
              </span>
            )}
            {(fixState === "done" || fixState === "error") && fixResult && (
              <div className="space-y-1">
                {/* 审核结论 */}
                <div className={`font-mono text-xs border-l-2 pl-2 ${fixResult.status === "approved" ? "border-amber text-amber" : "border-sev_high text-sev_high"}`}>
                  {fixResult.status === "approved" ? "DeepSeek 审核通过" : fixResult.status === "rejected" ? "DeepSeek 审核拒绝" : "修复失败"}
                  {fixResult.review_verdict && (
                    <span className="text-muted ml-1">— {fixResult.review_verdict}</span>
                  )}
                </div>

                {/* PR 链接 */}
                {fixResult.pr_url && (
                  <div className="font-mono text-xs">
                    <span className="text-muted">新 PR：</span>
                    <a
                      href={fixResult.pr_url}
                      target="_blank"
                      rel="noreferrer"
                      className="text-link underline hover:text-fg"
                    >
                      {fixResult.pr_url} ↗
                    </a>
                  </div>
                )}

                {/* 错误信息 */}
                {fixResult.error && (
                  <div className="font-mono text-xs text-sev_high">! {fixResult.error}</div>
                )}

                {/* 补丁展开 */}
                {fixResult.patch && (
                  <div>
                    <button
                      onClick={() => setShowPatch((v) => !v)}
                      className="font-mono text-xs text-link hover:text-fg"
                    >
                      {showPatch ? "[-]" : "[+]"} patch diff
                    </button>
                    {showPatch && (
                      <pre className="mt-1 max-h-64 overflow-auto whitespace-pre-wrap border border-line bg-bg p-3 font-mono text-xs leading-relaxed text-muted">
                        {fixResult.patch}
                      </pre>
                    )}
                  </div>
                )}

                {/* 重试 */}
                <button
                  onClick={() => { setFixState("idle"); setFixResult(null); }}
                  className="font-mono text-xs text-faint hover:text-fg"
                >
                  [retry]
                </button>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
