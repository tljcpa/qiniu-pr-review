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
      {/* 头部一行：高密度元信息 */}
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 border-b border-line px-3 py-1.5 text-xs">
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
      </div>

      <div className="px-3 py-2">
        <div className="font-bold text-fg">{finding.title}</div>

        {finding.detail && (
          <p className="mt-1 whitespace-pre-wrap text-muted">{finding.detail}</p>
        )}

        {finding.suggestion && (
          <div className="mt-2 border-l-2 border-amberdim pl-2 text-fg">
            <span className="text-amber">fix </span>
            <span className="whitespace-pre-wrap">{finding.suggestion}</span>
          </div>
        )}

        {/* 思维链可展开（亮点 3）——不用紫色，用与正文同系的低饱和色 */}
        {finding.reasoning && (
          <div className="mt-2 border-t border-line pt-2">
            <button
              onClick={() => setOpen((v) => !v)}
              className="text-xs text-link hover:text-fg"
            >
              {open ? "[-]" : "[+]"} reasoning_content (R1 思维链)
            </button>
            {open && (
              <pre className="mt-1 max-h-80 overflow-auto whitespace-pre-wrap border border-line bg-bg p-3 text-xs leading-relaxed text-muted">
                {finding.reasoning}
              </pre>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
