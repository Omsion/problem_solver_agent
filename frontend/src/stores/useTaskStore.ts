import { create } from "zustand";
import type { ProgressState } from "../types";
import { sseUrl } from "../api/client";

interface TaskState {
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
  /** Reset progress for a task. */
  resetProgress: (taskId: string) => void;
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

    es.addEventListener("error", (e: MessageEvent) => {
      const data = e.data ? JSON.parse(e.data) : { message: "SSE 连接错误" };
      get().updateProgress(taskId, {
        phase: "error",
        message: "处理失败",
        error: data.message,
      });
      get().disconnectSSE(taskId);
    });

    // Handle connection-level errors
    es.onerror = () => {
      // EventSource will auto-reconnect; only flag error if not already done
      const p = get().progress[taskId];
      if (p && p.phase !== "done" && p.phase !== "error") {
        get().updateProgress(taskId, {
          phase: "error",
          message: "连接中断",
          error: "SSE 连接失败",
        });
      }
      get().disconnectSSE(taskId);
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

  resetProgress: (taskId) => {
    get().disconnectSSE(taskId);
    set((s) => {
      const { [taskId]: _, ...rest } = s.progress;
      return { progress: rest };
    });
  },
}));
