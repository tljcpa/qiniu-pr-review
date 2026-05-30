import { useState } from "react";
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
}

export function FindingCard({ finding }: Props) {
  const [open, setOpen] = useState(false);

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
      </div>
    </div>
  );
}
