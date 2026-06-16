import { create } from "zustand";
import type { ProgressState } from "../types";
import { sseUrl, globalSseUrl } from "../api/client";

interface TaskState {
  /** Currently active task ID (persists across navigation) */
  activeTaskId: string | null;
  setActiveTaskId: (id: string | null) => void;

  /** Map of taskId → progress */
  progress: Record<string, ProgressState>;
  /** Active EventSource connections */
  connections: Record<string, EventSource>;
  /** Global EventSource connection for auto-import events */
  globalConnection: EventSource | null;
  /** Auto-imported task IDs that we've seen (for deduplication) */
  seenAutoImportedTasks: Set<string>;
  /** Callback when a new task is auto-imported */
  onAutoImportedTask: ((taskId: string, numImages: number) => void) | null;
  setOnAutoImportedTask: (cb: ((taskId: string, numImages: number) => void) | null) => void;

  /** Whether a remote client (e.g. phone via QR code) has connected */
  remoteConnected: boolean;
  setRemoteConnected: (val: boolean) => void;

  /** Connect SSE for a task and start streaming. */
  connectSSE: (taskId: string, thinking?: boolean) => void;
  /** Disconnect SSE for a task. */
  disconnectSSE: (taskId: string) => void;
  /** Connect global SSE for auto-import events. */
  connectGlobalSSE: () => void;
  /** Disconnect global SSE. */
  disconnectGlobalSSE: () => void;
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
  globalConnection: null,
  seenAutoImportedTasks: new Set(),
  onAutoImportedTask: null,
  setOnAutoImportedTask: (cb) => set({ onAutoImportedTask: cb }),
  remoteConnected: false,
  setRemoteConnected: (val) => set({ remoteConnected: val }),

  connectSSE: (taskId, thinking = true) => {
    // Close existing connection if any
    get().disconnectSSE(taskId);

    const url = sseUrl(taskId, thinking);
    const es = new EventSource(url);

    const handleAutoImported = (data: any) => {
      const importedTaskId = data.task_id;
      const numImages = data.num_images || 0;
      if (!importedTaskId) return;

      // Check if we've already seen this auto-imported task
      const seen = get().seenAutoImportedTasks;
      if (seen.has(importedTaskId)) return;

      set((s) => ({
        seenAutoImportedTasks: new Set([...s.seenAutoImportedTasks, importedTaskId])
      }));

      // Notify callback
      const cb = get().onAutoImportedTask;
      if (cb) {
        cb(importedTaskId, numImages);
      }
    };

    es.addEventListener("auto_imported", (e: MessageEvent) => {
      try {
        const data = JSON.parse(e.data);
        handleAutoImported(data);
      } catch { /* ignore parse errors */ }
    });

    es.addEventListener("init", (e: MessageEvent) => {
      const data = JSON.parse(e.data);
      const current = get().progress[taskId];
      // 只在还没有进度（首次连接）时设置 phase，避免 reconnect 时降级已有阶段
      if (!current || current.phase === "idle") {
        get().updateProgress(taskId, {
          phase: "classifying",
          message: `已接收 ${data.num_images} 张图片`,
        });
      } else {
        // 已有进度 → 只更新 message，不降级 phase
        get().updateProgress(taskId, {
          message: current.message,
        });
      }
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
        // Check if this error event is actually an auto_imported (fallback case)
        if (data.type === "auto_imported") {
          handleAutoImported(data);
          return;
        }
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
          case "auto_imported":
            handleAutoImported(data);
            break;
          case "init": {
            const current = get().progress[taskId];
            if (!current || current.phase === "idle") {
              get().updateProgress(taskId, { phase: "classifying", message: `已接收 ${data.num_images} 张图片` });
            } else {
              get().updateProgress(taskId, { message: current.message });
            }
            break;
          }
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
      progress: s.progress[taskId]
        ? s.progress // 已有进度 → 不要重置，保留现有 phase
        : { ...s.progress, [taskId]: emptyProgress() },
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

    const handleAutoImported = (data: any) => {
      const importedTaskId = data.task_id;
      const numImages = data.num_images || 0;
      if (!importedTaskId) return;

      const seen = get().seenAutoImportedTasks;
      if (seen.has(importedTaskId)) return;

      set((s) => ({
        seenAutoImportedTasks: new Set([...s.seenAutoImportedTasks, importedTaskId])
      }));

      const cb = get().onAutoImportedTask;
      if (cb) {
        cb(importedTaskId, numImages);
      }
    };

    const cleanup = () => {
      es.close();
      set((s) => {
        const { [taskId]: _, ...rest } = s.connections;
        return { connections: rest };
      });
    };

    es.addEventListener("auto_imported", (e: MessageEvent) => {
      try {
        const data = JSON.parse(e.data);
        handleAutoImported(data);
      } catch { /* ignore */ }
    });

    es.addEventListener("init", (e: MessageEvent) => {
      const data = JSON.parse(e.data);
      const current = get().progress[taskId];
      if (!current || current.phase === "idle") {
        get().updateProgress(taskId, { phase: "classifying", message: `已接收 ${data.num_images} 张图片` });
      } else {
        get().updateProgress(taskId, { message: current.message });
      }
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
        if (data.type === "auto_imported") {
          handleAutoImported(data);
          return;
        }
        get().updateProgress(taskId, { phase: "error", message: "处理失败", error: data.message || "未知错误" });
        cleanup();
      } catch { /* ignore */ }
    });

    es.onmessage = (e: MessageEvent) => {
      try {
        const data = JSON.parse(e.data);
        switch (data.type) {
          case "auto_imported":
            handleAutoImported(data);
            break;
          case "init": {
            const current = get().progress[taskId];
            if (!current || current.phase === "idle") {
              get().updateProgress(taskId, { phase: "classifying", message: `已接收 ${data.num_images} 张图片` });
            } else {
              get().updateProgress(taskId, { message: current.message });
            }
            break;
          }
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
            cleanup();
            break;
          case "error":
            get().updateProgress(taskId, { phase: "error", message: "处理失败", error: data.message });
            cleanup();
            break;
        }
      } catch { /* ignore */ }
    };

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

  connectGlobalSSE: () => {
    const existing = get().globalConnection;
    if (existing) {
      existing.close();
    }

    const url = globalSseUrl();
    const es = new EventSource(url);

    const handleAutoImported = (data: any) => {
      const taskId = data.task_id;
      const numImages = data.num_images || 0;
      if (!taskId) return;

      const seen = get().seenAutoImportedTasks;
      if (seen.has(taskId)) return;

      set((s) => ({
        seenAutoImportedTasks: new Set([...s.seenAutoImportedTasks, taskId])
      }));

      const cb = get().onAutoImportedTask;
      if (cb) {
        cb(taskId, numImages);
      }
    };

    es.addEventListener("auto_imported", (e: MessageEvent) => {
      try {
        const data = JSON.parse(e.data);
        handleAutoImported(data);
      } catch { /* ignore */ }
    });

    es.addEventListener("remote_connected", () => {
      get().setRemoteConnected(true);
    });

    es.onmessage = (e: MessageEvent) => {
      try {
        const data = JSON.parse(e.data);
        if (data.type === "auto_imported") {
          handleAutoImported(data);
        } else if (data.type === "remote_connected") {
          get().setRemoteConnected(true);
        }
      } catch { /* ignore */ }
    };

    es.onerror = () => {
      // 全局连接出错时，尝试在5秒后重连
      set({ globalConnection: null });
      setTimeout(() => {
        if (!get().globalConnection) {
          get().connectGlobalSSE();
        }
      }, 5000);
    };

    set({ globalConnection: es });
  },

  disconnectGlobalSSE: () => {
    const es = get().globalConnection;
    if (es) {
      es.close();
      set({ globalConnection: null });
    }
  },
}));
