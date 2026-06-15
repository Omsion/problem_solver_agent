import { useState, useCallback, useEffect } from "react";
import { HashRouter, Routes, Route, useNavigate } from "react-router-dom";
import { AppHeader } from "./components/layout/AppHeader";
import { SplitPanelLayout } from "./components/layout/SplitPanelLayout";
import { UploadZone } from "./components/upload/UploadZone";
import { FilePreviewList } from "./components/upload/FilePreviewList";
import { UploadActions } from "./components/upload/UploadActions";
import { ImageViewer } from "./components/viewer/ImageViewer";
import { ImageLightbox } from "./components/viewer/ImageLightbox";
import { OutputPanel } from "./components/output/OutputPanel";
import { TaskHistoryPage } from "./components/tasks/TaskHistoryPage";
import { useUploadStore } from "./stores/useUploadStore";
import { useTaskStore } from "./stores/useTaskStore";
import { createTask, getTask, listTasks } from "./api/client";

/**
 * 监听 hash 变化，返回当前 URL 中的 task 参数。
 * 使用 state + hashchange 事件确保 React Router 导航时能正确触发重渲染。
 */
function useHashTaskId(): string | null {
  const [taskId, setTaskId] = useState<string | null>(() => {
    const hash = window.location.hash;
    const qi = hash.indexOf("?");
    if (qi === -1) return null;
    return new URLSearchParams(hash.slice(qi + 1)).get("task");
  });

  useEffect(() => {
    const handler = () => {
      const hash = window.location.hash;
      const qi = hash.indexOf("?");
      const next = qi === -1 ? null : new URLSearchParams(hash.slice(qi + 1)).get("task");
      setTaskId(next);
    };
    window.addEventListener("hashchange", handler);
    return () => window.removeEventListener("hashchange", handler);
  }, []);

  return taskId;
}

function MainPage() {
  const activeTaskId = useTaskStore((s) => s.activeTaskId);
  const setActiveTaskId = useTaskStore((s) => s.setActiveTaskId);
  const connectSSE = useTaskStore((s) => s.connectSSE);
  const connectGlobalSSE = useTaskStore((s) => s.connectGlobalSSE);
  const disconnectGlobalSSE = useTaskStore((s) => s.disconnectGlobalSSE);
  const setOnAutoImportedTask = useTaskStore((s) => s.setOnAutoImportedTask);
  const updateProgress = useTaskStore((s) => s.updateProgress);
  const [isProcessing, setIsProcessing] = useState(false);
  const [isLoadingHistory, setIsLoadingHistory] = useState(false);
  const files = useUploadStore((s) => s.files);
  const navigate = useNavigate();

  const taskParam = useHashTaskId();
  const [historyImages, setHistoryImages] = useState<string[]>([]);
  const [hasNewAutoTask, setHasNewAutoTask] = useState(false);
  const [newTaskInfo, setNewTaskInfo] = useState<{ id: string; numImages: number } | null>(null);

  // 从历史记录跳转过来时，加载已完成任务的解答
  useEffect(() => {
    if (!taskParam) return;

    let cancelled = false;
    setIsLoadingHistory(true);
    (async () => {
      try {
        const { task, solution_content, image_urls } = await getTask(taskParam);
        if (cancelled) return;
        setActiveTaskId(taskParam);
        setHistoryImages(image_urls);
        updateProgress(taskParam, {
          phase: task.status === "completed" ? "done" : "error",
          message: task.status === "completed" ? "解答完成" : "任务失败",
          answer: solution_content,
          filename: task.filename,
          error: task.error_message,
        });
      } catch (err) {
        if (!cancelled) console.error("加载任务失败:", err);
      } finally {
        if (!cancelled) setIsLoadingHistory(false);
      }
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [taskParam]);

  // 当活跃任务 SSE 连接意外断开时自动重连（保留已累计进度）
  useEffect(() => {
    if (!activeTaskId) return;
    const p = useTaskStore.getState().progress[activeTaskId];
    const conn = useTaskStore.getState().connections[activeTaskId];
    if (!p || p.phase === "done" || p.phase === "error") return;
    if (conn) return; // already connected
    useTaskStore.getState().reconnectSSE(activeTaskId);
  }, [activeTaskId]);

  // 连接全局 SSE 监听自动导入事件
  useEffect(() => {
    // 设置回调处理自动导入的新任务
    setOnAutoImportedTask((taskId: string, numImages: number) => {
      setNewTaskInfo({ id: taskId, numImages });
      setHasNewAutoTask(true);
    });

    // 连接全局 SSE
    connectGlobalSSE();

    return () => {
      setOnAutoImportedTask(null);
      disconnectGlobalSSE();
    };
  }, [connectGlobalSSE, disconnectGlobalSSE, setOnAutoImportedTask]);

  // 初始化时获取最新任务时间作为基准
  useEffect(() => {
    const init = async () => {
      try {
        const { tasks } = await listTasks(1);
        if (tasks.length > 0) {
          // 预填充已见过的任务集合，避免页面加载时误触发
          const seen = useTaskStore.getState().seenAutoImportedTasks;
          tasks.forEach(t => seen.add(t.id));
        }
      } catch { /* ignore */ }
    };
    init();
  }, []);

  // 自动切换到新导入的任务
  useEffect(() => {
    if (!hasNewAutoTask || !newTaskInfo) return;

    // 如果用户正在处理其他任务，询问是否切换？
    // 这里我们直接切换，提供更好的体验
    const switchToNewTask = async () => {
      try {
        // 清除 URL 参数并重置状态
        navigate("/");
        setHistoryImages([]);

        // 加载新任务并连接 SSE
        const { task, solution_content, image_urls } = await getTask(newTaskInfo.id);
        setActiveTaskId(newTaskInfo.id);

        // 如果任务还在处理中，连接 SSE
        if (task.status === "pending" || task.status === "processing") {
          connectSSE(newTaskInfo.id, true);
          setHistoryImages(image_urls);
        } else {
          // 任务已完成，直接显示
          setHistoryImages(image_urls);
          updateProgress(newTaskInfo.id, {
            phase: task.status === "completed" ? "done" : "error",
            message: task.status === "completed" ? "解答完成" : "任务失败",
            answer: solution_content,
            filename: task.filename,
            error: task.error_message,
          });
        }
      } catch (err) {
        console.error("切换到新任务失败:", err);
      } finally {
        setHasNewAutoTask(false);
        setNewTaskInfo(null);
      }
    };

    switchToNewTask();
  }, [hasNewAutoTask, newTaskInfo, navigate, setActiveTaskId, connectSSE, updateProgress]);

  const handleStart = useCallback(async () => {
    if (files.length === 0) return;
    // 清除 URL 中的 task 参数，进入新任务模式
    navigate("/");
    setIsProcessing(true);
    setHistoryImages([]);
    setHasNewAutoTask(false);
    setNewTaskInfo(null);
    try {
      const fileObjs = files.map((f) => f.file);
      const { task_id } = await createTask(fileObjs);
      setActiveTaskId(task_id);
      connectSSE(task_id, true);
    } catch (err) {
      console.error("创建任务失败:", err);
      alert("创建任务失败，请重试");
    } finally {
      setIsProcessing(false);
    }
  }, [files, connectSSE, navigate, setActiveTaskId]);

  const handleDismissNewTask = useCallback(() => {
    setHasNewAutoTask(false);
    setNewTaskInfo(null);
  }, []);

  return (
    <div className="h-[calc(100vh-3.5rem)] flex flex-col">
      {/* 新任务提示条 */}
      {hasNewAutoTask && newTaskInfo && (
        <div className="bg-indigo-50 border-b border-indigo-200 px-4 py-2 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span className="inline-flex h-2 w-2 rounded-full bg-indigo-400 animate-pulse"></span>
            <span className="text-sm text-indigo-700">
              检测到新截图任务（{newTaskInfo.numImages} 张图片），正在加载...
            </span>
          </div>
          <button
            onClick={handleDismissNewTask}
            className="text-xs text-indigo-500 hover:text-indigo-600"
          >
            忽略
          </button>
        </div>
      )}

      <SplitPanelLayout
        left={
          <div className="flex flex-col gap-3 h-full">
            {isLoadingHistory ? (
              <div className="flex items-center justify-center h-full">
                <div className="flex flex-col items-center gap-3 text-gray-400">
                  <svg className="animate-spin w-8 h-8" viewBox="0 0 24 24">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                  </svg>
                  <p className="text-sm">加载历史任务...</p>
                </div>
              </div>
            ) : taskParam ? (
              historyImages.length > 0 ? (
                <div className="flex flex-col gap-2 h-full overflow-auto">
                  <div className="flex items-center justify-between px-1">
                    <span className="text-xs text-gray-400">{historyImages.length} 张题目图片</span>
                  <button
                    onClick={() => { setActiveTaskId(null); setHistoryImages([]); navigate("/"); }}
                    className="text-xs text-indigo-500 hover:text-indigo-600 cursor-pointer"
                  >
                      返回新建任务
                    </button>
                  </div>
                  {historyImages.map((url, i) => (
                    <img
                      key={i}
                      src={url}
                      alt={`题目图片 ${i + 1}`}
                      className="w-full rounded-lg border border-gray-200 object-contain bg-gray-100"
                    />
                  ))}
                </div>
              ) : (
                <div className="flex flex-col items-center justify-center gap-3 p-8 bg-gray-50 rounded-lg border border-gray-200 h-full text-center">
                  <svg className="w-12 h-12 text-gray-300" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
                      d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                  </svg>
                  <p className="text-sm text-gray-500 font-medium">查看历史解答</p>
                  <p className="text-xs text-gray-400">题目图片已清理，可查看右侧解答内容</p>
                  <button
                    onClick={() => { setActiveTaskId(null); setHistoryImages([]); navigate("/"); }}
                    className="mt-2 text-xs text-indigo-500 hover:text-indigo-600 cursor-pointer"
                  >
                    返回新建任务
                  </button>
                </div>
              )
            ) : files.length === 0 ? (
              <UploadZone />
            ) : (
              <>
                <ImageViewer />
                <FilePreviewList />
              </>
            )}
            {!taskParam && !isLoadingHistory && (
              <UploadActions onStart={handleStart} loading={isProcessing} />
            )}
          </div>
        }
        right={<OutputPanel taskId={activeTaskId} />}
      />
      <ImageLightbox />
    </div>
  );
}

function HistoryPage() {
  const navigate = useNavigate();

  const handleSelectTask = (taskId: string) => {
    navigate(`/?task=${taskId}`);
  };

  return (
    <div className="h-[calc(100vh-3.5rem)] bg-white">
      <TaskHistoryPage onSelectTask={handleSelectTask} />
    </div>
  );
}

export default function App() {
  return (
    <HashRouter>
      <div className="min-h-screen flex flex-col bg-gray-50">
        <AppHeader />
        <Routes>
          <Route path="/" element={<MainPage />} />
          <Route path="/history" element={<HistoryPage />} />
        </Routes>
      </div>
    </HashRouter>
  );
}
