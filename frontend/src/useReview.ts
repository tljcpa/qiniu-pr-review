import { useCallback, useRef, useState } from "react";
import type { ProgressEvent, ReviewMeta, ReviewReport } from "./types";

// 评审整体状态机
export type ReviewStatus = "idle" | "running" | "done" | "error";

interface ReviewState {
  status: ReviewStatus;
  events: ProgressEvent[];
  report: ReviewReport | null;
  meta: ReviewMeta | null;
  error: string | null;
}

const INITIAL: ReviewState = {
  status: "idle",
  events: [],
  report: null,
  meta: null,
  error: null,
};

// 这些事件类型到达即结束 SSE
const TERMINAL = new Set(["done", "error"]);

/**
 * 驱动一次 review：POST 建任务 -> EventSource 收 SSE 进度 -> done 后 GET 最终结果。
 * 把后端的流式事件转成前端可渲染的日志流 + 最终报告。
 */
// 发布（写回 PR）状态
export type PublishStatus = "idle" | "publishing" | "done" | "error";

export interface PublishState {
  status: PublishStatus;
  reviewUrl: string;
  inlineCount: number;
  summaryOnlyCount: number;
  error: string | null;
}

const PUBLISH_INITIAL: PublishState = {
  status: "idle",
  reviewUrl: "",
  inlineCount: 0,
  summaryOnlyCount: 0,
  error: null,
};

export function useReview() {
  const [state, setState] = useState<ReviewState>(INITIAL);
  const [publish, setPublish] = useState<PublishState>(PUBLISH_INITIAL);
  const esRef = useRef<EventSource | null>(null);
  const reviewIdRef = useRef<string>("");

  const reset = useCallback(() => {
    if (esRef.current) {
      esRef.current.close();
      esRef.current = null;
    }
    reviewIdRef.current = "";
    setPublish(PUBLISH_INITIAL);
    setState(INITIAL);
  }, []);

  // 把当前审查结果写回原 PR（inline 批注 + summary review）
  const publishToPR = useCallback(async () => {
    const reviewId = reviewIdRef.current;
    if (!reviewId) {
      return;
    }
    setPublish({ ...PUBLISH_INITIAL, status: "publishing" });
    try {
      const resp = await fetch(`/api/review/${reviewId}/publish`, { method: "POST" });
      const data = await resp.json();
      if (!resp.ok) {
        setPublish({
          ...PUBLISH_INITIAL,
          status: "error",
          error: String(data.detail ?? `HTTP ${resp.status}`),
        });
        return;
      }
      setPublish({
        status: "done",
        reviewUrl: data.review_url ?? "",
        inlineCount: data.inline_count ?? 0,
        summaryOnlyCount: data.summary_only_count ?? 0,
        error: null,
      });
    } catch (err) {
      setPublish({ ...PUBLISH_INITIAL, status: "error", error: String(err) });
    }
  }, []);

  const pushEvent = useCallback((type: string, data: Record<string, unknown>) => {
    setState((prev) => ({
      ...prev,
      events: [...prev.events, { type, data, ts: Date.now() }],
    }));
  }, []);

  const fetchFinalReport = useCallback(async (reviewId: string) => {
    try {
      const resp = await fetch(`/api/review/${reviewId}`);
      if (!resp.ok) {
        const text = await resp.text();
        setState((prev) => ({ ...prev, status: "error", error: text }));
        return;
      }
      const data = await resp.json();
      setState((prev) => ({
        ...prev,
        status: "done",
        report: data.report ?? null,
        meta: data.meta ?? null,
      }));
    } catch (err) {
      setState((prev) => ({ ...prev, status: "error", error: String(err) }));
    }
  }, []);

  const start = useCallback(
    async (url: string, useCache: boolean) => {
      reset();
      setState({ ...INITIAL, status: "running" });

      let reviewId: string;
      try {
        const resp = await fetch("/api/review", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ url, use_cache: useCache }),
        });
        if (!resp.ok) {
          throw new Error(`创建任务失败: HTTP ${resp.status}`);
        }
        const data = await resp.json();
        reviewId = data.review_id;
        reviewIdRef.current = reviewId;
      } catch (err) {
        setState((prev) => ({ ...prev, status: "error", error: String(err) }));
        return;
      }

      // 打开 SSE 流
      const es = new EventSource(`/api/review/${reviewId}/stream`);
      esRef.current = es;

      // 后端用具名事件（event: xxx），逐个监听
      const named = [
        "connected",
        "fetch_start",
        "fetch_done",
        "context_built",
        "cache_hit",
        "scan_start",
        "scan_done",
        "deep_read_start",
        "finding_verdict",
        "review_done",
        "done",
        "error",
      ];
      for (const name of named) {
        es.addEventListener(name, (ev: MessageEvent) => {
          let data: Record<string, unknown> = {};
          try {
            data = JSON.parse(ev.data);
          } catch {
            data = { raw: ev.data };
          }
          pushEvent(name, data);
          if (TERMINAL.has(name)) {
            es.close();
            esRef.current = null;
            if (name === "error") {
              setState((prev) => ({
                ...prev,
                status: "error",
                error: String((data as { message?: string }).message ?? "评审失败"),
              }));
            } else {
              // done：拉最终报告
              void fetchFinalReport(reviewId);
            }
          }
        });
      }

      es.onerror = () => {
        // EventSource 在流正常结束时也会触发 onerror；只有当我们还没拿到结果时才算错误
        setState((prev) => {
          if (prev.status === "running") {
            return { ...prev, status: "error", error: "连接中断" };
          }
          return prev;
        });
        es.close();
        esRef.current = null;
      };
    },
    [reset, pushEvent, fetchFinalReport]
  );

  return { state, start, reset, publish, publishToPR };
}
