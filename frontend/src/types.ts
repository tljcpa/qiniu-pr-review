// 与后端 app/models/finding.py 的 schema 对应（接口冻结契约）

export type Severity = "high" | "medium" | "low";
export type Confidence = "high" | "medium" | "low";
export type Category = "bug" | "security" | "logic" | "maintainability" | "style";

export interface Finding {
  file: string;
  line_hint: string;
  severity: Severity;
  category: Category;
  title: string;
  detail: string;
  suggestion: string;
  confidence: Confidence;
  confidence_score: number;
  verdict: string;
  deep_read: boolean;
  reasoning: string | null;
  cross_check: string;
}

export interface ReviewReport {
  summary: string;
  context_level: string;
  findings: Finding[];
  total_findings: number;
  high_count: number;
  medium_count: number;
  low_count: number;
  usage: Record<string, number>;
  trace: string[];
}

export interface ReviewMeta {
  from_cache: boolean;
  cached_files: number;
  reviewed_files: number;
  cache_stats: Record<string, number>;
}

// SSE 进度事件：一条日志流里的一行
export interface ProgressEvent {
  type: string; // connected / fetch_start / fetch_done / context_built / scan_start / scan_done / deep_read_start / finding_verdict / review_done / done / error / cache_hit
  data: Record<string, unknown>;
  ts: number;
}
