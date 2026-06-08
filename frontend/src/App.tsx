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
import { createTask, getTask } from "./api/client";

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
  const [isProcessing, setIsProcessing] = useState(false);
  const [isLoadingHistory, setIsLoadingHistory] = useState(false);
  const files = useUploadStore((s) => s.files);
  const connectSSE = useTaskStore((s) => s.connectSSE);
  const updateProgress = useTaskStore((s) => s.updateProgress);
  const connections = useTaskStore((s) => s.connections);
  const progress = useTaskStore((s) => s.progress);
  const navigate = useNavigate();

  const taskParam = useHashTaskId();
  const [historyImages, setHistoryImages] = useState<string[]>([]);

  // 从历史记录跳转过来时，加载已完成任务的解答
  useEffect(() => {
    if (!taskParam) {
      // 回到首页但没有 task 参数 — 如果有进行中的任务且 SSE 已断连，尝试重连
      if (activeTaskId && progress[activeTaskId]?.phase !== "done" && progress[activeTaskId]?.phase !== "error") {
        if (!connections[activeTaskId]) {
          connectSSE(activeTaskId, true);
        }
      }
      return;
    }
    let cancelled = false;
    // 仅在首次加载（无活跃任务）时显示 loading
    if (!activeTaskId || activeTaskId !== taskParam) {
      setIsLoadingHistory(!activeTaskId);
    }
    (async () => {
      setIsLoadingHistory(true);
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
  }, [taskParam, updateProgress, setActiveTaskId, activeTaskId, progress, connections, connectSSE]);

  const handleStart = useCallback(async () => {
    if (files.length === 0) return;
    // 清除 URL 中的 task 参数，进入新任务模式
    navigate("/");
    setIsProcessing(true);
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
  }, [files, connectSSE, navigate]);

  return (
    <div className="h-[calc(100vh-3.5rem)] flex flex-col">
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
              // 查看历史任务时，左侧显示任务元信息（原始图片已被后端清理）
              <div className="flex flex-col items-center justify-center gap-3 p-8 bg-gray-50 rounded-lg border border-gray-200 h-full text-center">
                <svg className="w-12 h-12 text-gray-300" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
                    d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                </svg>
                <p className="text-sm text-gray-500 font-medium">查看历史解答</p>
                <p className="text-xs text-gray-400">题目图片已清理，可查看右侧解答内容</p>
                <button
                  onClick={() => {
                    setActiveTaskId(null);
                    navigate("/");
                  }}
                  className="mt-2 text-xs text-indigo-500 hover:text-indigo-600 cursor-pointer"
                >
                  返回新建任务
                </button>
              </div>
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
