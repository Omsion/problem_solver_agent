import type { SSEEvent } from "../../types";

/**
 * 任务流连接管理。
 *
 * 背景（缺陷 C）：原先 `useTaskStore` 里 `connectSSE` 与 `reconnectSSE` 是两份
 * 逐行重复的实现，且 `onerror` 只把连接从 store 里删掉、不重连；而"重连"的
 * useEffect 依赖 `[activeTaskId]`，任务 id 不变就再也不会触发。手机端一旦掉线
 * （切后台、锁屏、Wi-Fi 抖动是常态），就永久停在"等待任务开始"。
 *
 * 这里把连接逻辑收敛成唯一实现，并补上：
 *   - 指数退避自动重连（1s → 2s → 4s，上限 15s）
 *   - 页面重新可见（visibilitychange）时立即重连
 *   - 后端已记录取消/失败等终态时不再重连
 *   - 明确的连接状态回调，供 UI 显示"重连中／失败·重试"
 */

export type StreamStatus = "connecting" | "open" | "reconnecting" | "closed" | "failed";

export interface StreamHandlers {
  /** 收到任意业务事件（已解析） */
  onEvent: (event: SSEEvent) => void;
  /** 连接状态变化 */
  onStatus?: (status: StreamStatus, info?: { attempt?: number; reason?: string }) => void;
}

export interface TaskStream {
  /** 主动关闭，不再重连 */
  close: () => void;
  /** 立即重试（用户点"重试"时） */
  retry: () => void;
  /** 当前状态 */
  status: () => StreamStatus;
}

const BASE_DELAY_MS = 1000;
const MAX_DELAY_MS = 15000;
/** 服务端返回这些事件后说明任务已进入终态，不必再重连 */
const TERMINAL_EVENTS = new Set(["done", "error", "cancelled"]);

export function createTaskStream(
  taskId: string,
  buildUrl: (taskId: string) => string,
  handlers: StreamHandlers,
): TaskStream {
  let source: EventSource | null = null;
  let attempt = 0;
  let closedByCaller = false;
  let finished = false;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let currentStatus: StreamStatus = "connecting";

  const setStatus = (status: StreamStatus, info?: { attempt?: number; reason?: string }) => {
    currentStatus = status;
    handlers.onStatus?.(status, info);
  };

  const clearTimer = () => {
    if (timer !== null) {
      clearTimeout(timer);
      timer = null;
    }
  };

  const teardown = () => {
    if (source) {
      source.close();
      source = null;
    }
  };

  const scheduleReconnect = (reason: string) => {
    if (closedByCaller || finished) return;
    clearTimer();
    attempt += 1;
    const delay = Math.min(BASE_DELAY_MS * 2 ** (attempt - 1), MAX_DELAY_MS);
    setStatus("reconnecting", { attempt, reason });
    timer = setTimeout(() => {
      timer = null;
      open();
    }, delay);
  };

  const handleMessage = (raw: string) => {
    let data: SSEEvent;
    try {
      data = JSON.parse(raw) as SSEEvent;
    } catch {
      return; // 忽略无法解析的负载（例如心跳注释）
    }
    if (data && typeof data.type === "string" && TERMINAL_EVENTS.has(data.type)) {
      finished = true;
    }
    handlers.onEvent(data);
    if (finished) {
      teardown();
      setStatus("closed");
    }
  };

  const attach = (es: EventSource) => {
    // 后端用 `event: <type>` 命名事件，因此每种类型都要单独监听；
    // 同时保留 onmessage 以兼容不带 event 行的默认消息。
    const named = [
      "init",
      "status",
      "reasoning",
      "chunk",
      "timings",
      "done",
      "error",
      "cancelled",
      "verified",
      "auto_imported",
    ];
    for (const name of named) {
      es.addEventListener(name, (e: Event) => {
        const message = e as MessageEvent;
        if (typeof message.data === "string") handleMessage(message.data);
      });
    }
    es.onmessage = (e: MessageEvent) => {
      if (typeof e.data === "string") handleMessage(e.data);
    };
    es.onopen = () => {
      attempt = 0;
      setStatus("open");
    };
    es.onerror = () => {
      teardown();
      if (finished) {
        setStatus("closed");
        return;
      }
      // 达到一定次数后标记为需要人工重试，但保留自动重连
      if (attempt >= 4) {
        setStatus("failed", { attempt, reason: "连接多次失败" });
      }
      scheduleReconnect("连接中断");
    };
  };

  function open() {
    if (closedByCaller || finished) return;
    teardown();
    setStatus(attempt === 0 ? "connecting" : "reconnecting", { attempt });
    try {
      const es = new EventSource(buildUrl(taskId));
      source = es;
      attach(es);
    } catch (err) {
      scheduleReconnect(err instanceof Error ? err.message : "EventSource 创建失败");
    }
  }

  const onVisibility = () => {
    if (document.visibilityState !== "visible") return;
    if (closedByCaller || finished) return;
    if (source) return; // 连接还在，无需处理
    clearTimer();
    setStatus("reconnecting", { attempt, reason: "页面恢复可见" });
    open();
  };

  document.addEventListener("visibilitychange", onVisibility);

  const close = () => {
    closedByCaller = true;
    clearTimer();
    teardown();
    document.removeEventListener("visibilitychange", onVisibility);
    setStatus("closed");
  };

  const retry = () => {
    if (closedByCaller || finished) return;
    attempt = 0;
    clearTimer();
    open();
  };

  open();

  return { close, retry, status: () => currentStatus };
}
