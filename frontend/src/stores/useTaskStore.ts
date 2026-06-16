import { create } from "zustand";
import type { ProgressState } from "../types";
import { sseUrl, globalSseUrl } from "../api/client";

interface TaskState {
  activeTaskId: string | null;
  setActiveTaskId: (id: string | null) => void;

  progress: Record<string, ProgressState>;
  connections: Record<string, EventSource>;
  globalConnection: EventSource | null;
  seenAutoImportedTasks: Set<string>;
  onAutoImportedTask: ((taskId: string, numImages: number) => void) | null;
  setOnAutoImportedTask: (cb: ((taskId: string, numImages: number) => void) | null) => void;

  remoteConnected: boolean;
  setRemoteConnected: (val: boolean) => void;

  connectSSE: (taskId: string, thinking?: boolean) => void;
  disconnectSSE: (taskId: string) => void;
  connectGlobalSSE: () => void;
  disconnectGlobalSSE: () => void;
  updateProgress: (taskId: string, patch: Partial<ProgressState>) => void;
  reconnectSSE: (taskId: string) => void;
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

  resetProgress: (taskId) => set((s) => ({
    progress: { ...s.progress, [taskId]: emptyProgress() }
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
    disconnectSSE(taskId);

    const url = sseUrl(taskId, thinking);
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
        updateProgress(taskId, {
          phase: "classifying",
          message: `已接收 ${data.num_images} 张图片`,
        });
      }
    });

    es.addEventListener("status", (e: MessageEvent) => {
      const data = JSON.parse(e.data);
      updateProgress(taskId, {
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
      updateProgress(taskId, {
        phase: "done",
        message: "解答完成",
        filename: data.filename,
      });
      get().disconnectSSE(taskId);
    });

    es.addEventListener("error", (e: Event) => {
      if (!("data" in e) || !(e as MessageEvent).data) return;
      try {
        const data = JSON.parse((e as MessageEvent).data);
        if (data.type === "auto_imported") {
          handleAutoImported(data);
          return;
        }
        updateProgress(taskId, {
          phase: "error",
          message: "处理失败",
          error: data.message || "未知错误",
        });
        get().disconnectSSE(taskId);
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
              updateProgress(taskId, { phase: "classifying", message: `已接收 ${data.num_images} 张图片` });
            }
            break;
          }
          case "status":
            updateProgress(taskId, { phase: data.phase, message: data.message });
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
            updateProgress(taskId, { phase: "done", message: "解答完成", filename: data.filename });
            get().disconnectSSE(taskId);
            break;
          case "error":
            updateProgress(taskId, { phase: "error", message: "处理失败", error: data.message });
            get().disconnectSSE(taskId);
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
      progress: s.progress[taskId]
        ? s.progress
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

  reconnectSSE: (taskId) => {
    if (get().connections[taskId]) return;
    const url = sseUrl(taskId, true);
    const es = new EventSource(url);
    const { updateProgress } = get();

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
        updateProgress(taskId, { phase: "classifying", message: `已接收 ${data.num_images} 张图片` });
      }
    });

    es.addEventListener("status", (e: MessageEvent) => {
      const data = JSON.parse(e.data);
      updateProgress(taskId, { phase: data.phase, message: data.message });
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
      updateProgress(taskId, { phase: "done", message: "解答完成", filename: data.filename });
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
        updateProgress(taskId, { phase: "error", message: "处理失败", error: data.message || "未知错误" });
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
              updateProgress(taskId, { phase: "classifying", message: `已接收 ${data.num_images} 张图片` });
            }
            break;
          }
          case "status":
            updateProgress(taskId, { phase: data.phase, message: data.message });
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
            updateProgress(taskId, { phase: "done", message: "解答完成", filename: data.filename });
            cleanup();
            break;
          case "error":
            updateProgress(taskId, { phase: "error", message: "处理失败", error: data.message });
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

    const handleRemoteConnected = () => {
      set({ remoteConnected: true });
    };

    es.addEventListener("auto_imported", (e: MessageEvent) => {
      try {
        const data = JSON.parse(e.data);
        handleAutoImported(data);
      } catch { /* ignore */ }
    });

    es.addEventListener("remote_connected", () => {
      handleRemoteConnected();
    });

    es.onmessage = (e: MessageEvent) => {
      try {
        const data = JSON.parse(e.data);
        if (data.type === "auto_imported") {
          handleAutoImported(data);
        } else if (data.type === "remote_connected") {
          handleRemoteConnected();
        }
      } catch { /* ignore */ }
    };

    es.onerror = () => {
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
