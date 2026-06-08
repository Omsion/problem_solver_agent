import { create } from "zustand";
import type { ProgressState } from "../types";
import { sseUrl } from "../api/client";

interface TaskState {
  /** Currently active task ID (persists across navigation) */
  activeTaskId: string | null;
  setActiveTaskId: (id: string | null) => void;

  /** Map of taskId → progress */
  progress: Record<string, ProgressState>;
  /** Active EventSource connections */
  connections: Record<string, EventSource>;

  /** Connect SSE for a task and start streaming. */
  connectSSE: (taskId: string, thinking?: boolean) => void;
  /** Disconnect SSE for a task. */
  disconnectSSE: (taskId: string) => void;
  /** Update a single task's progress (used by SSE event handler). */
  updateProgress: (taskId: string, patch: Partial<ProgressState>) => void;
  /** Reconnect SSE for a task without resetting accumulated progress. */
  reconnectSSE: (taskId: string) => void;
}

function emptyProgress(): ProgressState {
  return {
    phase: "idle",
    message: "",
    thinking: "",
    answer: "",
    filename: null,
    error: null,
  };
}

export const useTaskStore = create<TaskState>((set, get) => ({
  activeTaskId: null,
  setActiveTaskId: (id) => set({ activeTaskId: id }),

  progress: {},
  connections: {},

  connectSSE: (taskId, thinking = true) => {
    // Close existing connection if any
    get().disconnectSSE(taskId);

    const url = sseUrl(taskId, thinking);
    const es = new EventSource(url);

    es.addEventListener("init", (e: MessageEvent) => {
      const data = JSON.parse(e.data);
      get().updateProgress(taskId, {
        phase: "classifying",
        message: `已接收 ${data.num_images} 张图片`,
      });
    });

    es.addEventListener("status", (e: MessageEvent) => {
      const data = JSON.parse(e.data);
      get().updateProgress(taskId, {
        phase: data.phase,
        message: data.message,
      });
    });

    es.addEventListener("reasoning", (e: MessageEvent) => {
      const data = JSON.parse(e.data);
      set((s) => {
        const p = s.progress[taskId] ?? emptyProgress();
        return {
          progress: {
            ...s.progress,
            [taskId]: { ...p, thinking: p.thinking + (data.content ?? "") },
          },
        };
      });
    });

    es.addEventListener("chunk", (e: MessageEvent) => {
      const data = JSON.parse(e.data);
      set((s) => {
        const p = s.progress[taskId] ?? emptyProgress();
        return {
          progress: {
            ...s.progress,
            [taskId]: { ...p, answer: p.answer + (data.content ?? "") },
          },
        };
      });
    });

    es.addEventListener("done", (e: MessageEvent) => {
      const data = JSON.parse(e.data);
      get().updateProgress(taskId, {
        phase: "done",
        message: "解答完成",
        filename: data.filename,
      });
      get().disconnectSSE(taskId);
    });

    es.addEventListener("error", (e: Event) => {
      // 仅处理 SSE event: error 消息（有 data 的 MessageEvent）
      // 连接级错误由 es.onerror 处理，此处忽略
      if (!("data" in e) || !(e as MessageEvent).data) return;
      try {
        const data = JSON.parse((e as MessageEvent).data);
        get().updateProgress(taskId, {
          phase: "error",
          message: "处理失败",
          error: data.message || "未知错误",
        });
        get().disconnectSSE(taskId);
      } catch {
        // ignore parse errors
      }
    });

    // Fallback — 捕获缺少 event: 前缀的消息（向后兼容）
    es.onmessage = (e: MessageEvent) => {
      try {
        const data = JSON.parse(e.data);
        switch (data.type) {
          case "init":
            get().updateProgress(taskId, { phase: "classifying", message: `已接收 ${data.num_images} 张图片` });
            break;
          case "status":
            get().updateProgress(taskId, { phase: data.phase, message: data.message });
            break;
          case "reasoning":
            set((s) => {
              const p = s.progress[taskId] ?? emptyProgress();
              return { progress: { ...s.progress, [taskId]: { ...p, thinking: p.thinking + (data.content ?? "") } } };
            });
            break;
          case "chunk":
            set((s) => {
              const p = s.progress[taskId] ?? emptyProgress();
              return { progress: { ...s.progress, [taskId]: { ...p, answer: p.answer + (data.content ?? "") } } };
            });
            break;
          case "done":
            get().updateProgress(taskId, { phase: "done", message: "解答完成", filename: data.filename });
            get().disconnectSSE(taskId);
            break;
          case "error":
            get().updateProgress(taskId, { phase: "error", message: "处理失败", error: data.message });
            get().disconnectSSE(taskId);
            break;
        }
      } catch {
        // ignore unparseable messages
      }
    };

    // 连接级错误（断连/超时）— 仅断开，不标记任务失败
    // 由 App 层 reconnectSSE 负责恢复
    es.onerror = () => {
      set((s) => {
        const { [taskId]: _, ...rest } = s.connections;
        return { connections: rest };
      });
    };

    set((s) => ({
      connections: { ...s.connections, [taskId]: es },
      progress: { ...s.progress, [taskId]: emptyProgress() },
    }));
  },

  disconnectSSE: (taskId) => {
    const es = get().connections[taskId];
    if (es) {
      es.close();
      set((s) => {
        const { [taskId]: _, ...rest } = s.connections;
        return { connections: rest };
      });
    }
  },

  updateProgress: (taskId, patch) => {
    set((s) => {
      const prev = s.progress[taskId] ?? emptyProgress();
      return {
        progress: { ...s.progress, [taskId]: { ...prev, ...patch } },
      };
    });
  },

  resetProgress: (taskId: string) => {
    const es = get().connections[taskId];
    if (es) es.close();
    set((s) => {
      const { [taskId]: _, ...rest } = s.connections;
      return { connections: rest };
    });
    set((s) => {
      const { [taskId]: _, ...rest } = s.progress;
      return { progress: rest };
    });
  },

  reconnectSSE: (taskId) => {
    if (get().connections[taskId]) return; // already connected
    const url = sseUrl(taskId, true);
    const es = new EventSource(url);

    const cleanup = () => {
      es.close();
      set((s) => {
        const { [taskId]: _, ...rest } = s.connections;
        return { connections: rest };
      });
    };

    es.addEventListener("init", (e: MessageEvent) => {
      const data = JSON.parse(e.data);
      get().updateProgress(taskId, { phase: "classifying", message: `已接收 ${data.num_images} 张图片` });
    });

    es.addEventListener("status", (e: MessageEvent) => {
      const data = JSON.parse(e.data);
      get().updateProgress(taskId, { phase: data.phase, message: data.message });
    });

    es.addEventListener("reasoning", (e: MessageEvent) => {
      const data = JSON.parse(e.data);
      set((s) => {
        const p = s.progress[taskId] ?? emptyProgress();
        return { progress: { ...s.progress, [taskId]: { ...p, thinking: p.thinking + (data.content ?? "") } } };
      });
    });

    es.addEventListener("chunk", (e: MessageEvent) => {
      const data = JSON.parse(e.data);
      set((s) => {
        const p = s.progress[taskId] ?? emptyProgress();
        return { progress: { ...s.progress, [taskId]: { ...p, answer: p.answer + (data.content ?? "") } } };
      });
    });

    es.addEventListener("done", (e: MessageEvent) => {
      const data = JSON.parse(e.data);
      get().updateProgress(taskId, { phase: "done", message: "解答完成", filename: data.filename });
      cleanup();
    });

    es.addEventListener("error", (e: Event) => {
      if (!("data" in e) || !(e as MessageEvent).data) return;
      try {
        const data = JSON.parse((e as MessageEvent).data);
        get().updateProgress(taskId, { phase: "error", message: "处理失败", error: data.message || "未知错误" });
        cleanup();
      } catch { /* ignore */ }
    });

    es.onerror = () => {
      set((s) => {
        const { [taskId]: _, ...rest } = s.connections;
        return { connections: rest };
      });
    };

    set((s) => ({
      connections: { ...s.connections, [taskId]: es },
    }));
  },
}));
