import { create } from "zustand";
import type { ProgressState, StageTimings } from "../types";
import { sseUrl, globalSseUrl } from "../api/client";
import { createTaskStream, type StreamStatus, type TaskStream } from "../features/stream/taskStream";
import { appendBounded } from "../lib/streamBuffer";

function emptyProgress(): ProgressState {
  return {
    phase: "idle",
    message: "",
    thinking: "",
    answer: "",
    filename: null,
    error: null,
    timings: null,
    streamStatus: "connecting",
    reconnectAttempt: 0,
    answerCard: null,
    verification: null,
    verifying: false,
  };
}

interface TaskState {
  activeTaskId: string | null;
  setActiveTaskId: (id: string | null) => void;

  progress: Record<string, ProgressState>;
  streams: Record<string, TaskStream>;
  globalConnection: EventSource | null;
  seenAutoImportedTasks: Set<string>;
  onAutoImportedTask: ((taskId: string, numImages: number) => void) | null;
  setOnAutoImportedTask: (cb: ((taskId: string, numImages: number) => void) | null) => void;

  remoteConnected: boolean;
  setRemoteConnected: (val: boolean) => void;

  connectSSE: (taskId: string, thinking?: boolean) => void;
  disconnectSSE: (taskId: string) => void;
  /** 手动重试当前任务的流连接 */
  retryStream: (taskId: string) => void;
  connectGlobalSSE: () => void;
  disconnectGlobalSSE: () => void;
  updateProgress: (taskId: string, patch: Partial<ProgressState>) => void;
  resetProgress: (taskId: string) => void;
}

export const useTaskStore = create<TaskState>((set, get) => ({
  activeTaskId: null,
  setActiveTaskId: (id) => set({ activeTaskId: id }),

  progress: {},
  streams: {},
  globalConnection: null,
  seenAutoImportedTasks: new Set(),
  onAutoImportedTask: null,
  setOnAutoImportedTask: (cb) => set({ onAutoImportedTask: cb }),
  remoteConnected: false,
  setRemoteConnected: (val) => set({ remoteConnected: val }),

  resetProgress: (taskId) =>
    set((s) => ({
      progress: { ...s.progress, [taskId]: emptyProgress() },
    })),

  updateProgress: (taskId, patch) => {
    set((s) => {
      const prev = s.progress[taskId] ?? emptyProgress();
      return {
        progress: { ...s.progress, [taskId]: { ...prev, ...patch } },
      };
    });
  },

  connectSSE: (taskId, thinking = true) => {
    const { disconnectSSE, updateProgress } = get();
    // 同一任务已有连接就不重复建（幂等），这是缺陷 B 的关键：
    // 快速切换历史任务时可能对同一 id 多次触发连接逻辑。
    if (get().streams[taskId]) {
      return;
    }
    disconnectSSE(taskId);

    updateProgress(taskId, { streamStatus: "connecting", reconnectAttempt: 0 });

    const stream = createTaskStream(taskId, (id) => sseUrl(id, thinking), {
      onEvent: (data) => {
        const current = get().progress[taskId];
        switch (data.type) {
          case "init":
            if (!current || current.phase === "idle") {
              updateProgress(taskId, {
                phase: "classifying",
                message: `已接收 ${data.num_images ?? 0} 张图片`,
              });
            }
            break;
          case "status":
            updateProgress(taskId, { phase: data.phase ?? "classifying", message: data.message ?? "" });
            break;
          case "reasoning":
            set((s) => {
              const p = s.progress[taskId] ?? emptyProgress();
              return {
                progress: { ...s.progress, [taskId]: { ...p, thinking: appendBounded(p.thinking, data.content ?? "") } },
              };
            });
            break;
          case "chunk":
            set((s) => {
              const p = s.progress[taskId] ?? emptyProgress();
              return {
                progress: { ...s.progress, [taskId]: { ...p, answer: appendBounded(p.answer, data.content ?? "") } },
              };
            });
            break;
          case "timings":
            updateProgress(taskId, { timings: (data as { timings?: StageTimings }).timings ?? null });
            break;
          case "done":
            updateProgress(taskId, {
              phase: "done",
              message: "解答完成",
              filename: data.filename ?? null,
              answerCard: data.answer_card ?? null,
              resolved: data.resolved ?? false,
              // done 事件本身也带 timings（单独一条 timings 事件可能因断线丢失）
              timings: data.timings ?? get().progress[taskId]?.timings ?? null,
              // 处理结束，清掉可能残留的"重连中"状态
              streamStatus: "closed",
              verifying: false,
            });
            break;
          case "verified":
            updateProgress(taskId, {
              verification: data.verification ?? null,
              verifying: false,
            });
            break;
          case "cancelled":
            updateProgress(taskId, {
              phase: "cancelled",
              message: data.message ?? "任务已取消",
            });
            break;
          case "error":
            updateProgress(taskId, {
              phase: "error",
              message: "处理失败",
              error: data.message ?? "未知错误",
            });
            break;
          default:
            break;
        }
      },
      onStatus: (status: StreamStatus, info) => {
        if (status === "closed") {
          // 连接结束（完成/取消/失败）后从注册表移除，允许后续重试
          set((s) => {
            const { [taskId]: _removed, ...rest } = s.streams;
            return { streams: rest };
          });
          return;
        }
        updateProgress(taskId, {
          streamStatus: status,
          reconnectAttempt: info?.attempt ?? 0,
        });
        if (status === "reconnecting" && info?.reason === "连接中断") {
          const current = get().progress[taskId];
          if (current && !current.error && current.phase !== "error") {
            updateProgress(taskId, {
              message: `连接中断，正在重连（第 ${info.attempt} 次）…`,
            });
          }
        }
      },
    });

    set((s) => ({
      streams: { ...s.streams, [taskId]: stream },
      progress: s.progress[taskId] ? s.progress : { ...s.progress, [taskId]: emptyProgress() },
    }));
  },

  retryStream: (taskId) => {
    const stream = get().streams[taskId];
    if (stream) {
      stream.retry();
      return;
    }
    get().connectSSE(taskId, true);
  },

  disconnectSSE: (taskId) => {
    const stream = get().streams[taskId];
    if (stream) {
      stream.close();
      set((s) => {
        const { [taskId]: _removed, ...rest } = s.streams;
        return { streams: rest };
      });
    }
  },

  connectGlobalSSE: () => {
    const existing = get().globalConnection;
    if (existing) {
      existing.close();
    }

    // 每次新建全局连接都先复位远程状态：旧连接残留的 true 会让扫码按钮
    // 永远显示"已连接"（缺陷 A 的成因之一）
    set({ remoteConnected: false });

    const es = new EventSource(globalSseUrl());

    const markSeenAndNotify = (taskId: string, numImages: number) => {
      const seen = get().seenAutoImportedTasks;
      if (seen.has(taskId)) return;
      set((s) => ({
        seenAutoImportedTasks: new Set([...s.seenAutoImportedTasks, taskId]),
      }));
      get().onAutoImportedTask?.(taskId, numImages);
    };

    const handleAutoImported = (data: { task_id?: string; num_images?: number }) => {
      if (!data?.task_id) return;
      markSeenAndNotify(data.task_id, data.num_images ?? 0);
    };

    es.addEventListener("auto_imported", (e: MessageEvent) => {
      try {
        handleAutoImported(JSON.parse(e.data));
      } catch { /* ignore */ }
    });

    es.addEventListener("remote_connected", () => set({ remoteConnected: true }));
    es.addEventListener("remote_disconnected", () => set({ remoteConnected: false }));

    es.onmessage = (e: MessageEvent) => {
      try {
        const data = JSON.parse(e.data);
        if (data.type === "auto_imported") {
          handleAutoImported(data);
        } else if (data.type === "remote_connected") {
          set({ remoteConnected: true });
        } else if (data.type === "remote_disconnected") {
          set({ remoteConnected: false });
        }
      } catch { /* ignore */ }
    };

    es.onerror = () => {
      set({ globalConnection: null, remoteConnected: false });
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
      set({ globalConnection: null, remoteConnected: false });
    }
  },
}));
