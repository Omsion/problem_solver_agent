import { useState, useCallback, useEffect, useRef, Suspense, lazy } from "react";
import { HashRouter, Routes, Route, useNavigate, useParams } from "react-router-dom";
import { AppHeader } from "./components/layout/AppHeader";
import { SplitPanelLayout } from "./components/layout/SplitPanelLayout";
import { RequireAdmin, RequireAuth } from "./components/auth/AuthRoutes";
import { UploadZone } from "./components/upload/UploadZone";
import { FilePreviewList } from "./components/upload/FilePreviewList";
import { UploadActions } from "./components/upload/UploadActions";
import { ImageViewer } from "./components/viewer/ImageViewer";
import { ImageLightbox } from "./components/viewer/ImageLightbox";
import { OutputPanel } from "./components/output/OutputPanel";
import { TaskHistoryPage } from "./components/tasks/TaskHistoryPage";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { useUploadStore } from "./stores/useUploadStore";
import { useTaskStore } from "./stores/useTaskStore";
import { useLayoutStore } from "./stores/useLayoutStore";
import { createTask, getTask, listTasks } from "./lib/api";
import { isTerminalStatus } from "./lib/taskStatus";

// 设置页只在需要时加载：它依赖 @tanstack/react-query，把这份体积移出首屏
const SettingsPage = lazy(() =>
  import("./components/settings/SettingsPage").then((m) => ({ default: m.SettingsPage })),
);

// 登录页 / 用量页 / 管理看板同样按需加载，它们都在首屏路径之外；
// 静态导入会把 react-query 之外的表单与表格代码塞进首屏分包。
const LoginPage = lazy(() => import("./pages/LoginPage"));
const UsagePage = lazy(() => import("./pages/UsagePage"));
const AdminPage = lazy(() => import("./pages/AdminPage"));

/**
 * 任务页面。
 *
 * 缺陷 B 的修复要点：任务 id 来自路由 path 参数，加载逻辑是**幂等**的——
 * 不再用 `loadingTaskRef` 做"我加载过就不再加载"的早退守卫。那个守卫在快速
 * 交替点击历史记录时会让整段加载被跳过，页面停在"等待任务开始"，必须手动
 * 刷新才恢复。
 */
function TaskPage({ taskId }: { taskId: string }) {
  const navigate = useNavigate();
  const setActiveTaskId = useTaskStore((s) => s.setActiveTaskId);
  const connectSSE = useTaskStore((s) => s.connectSSE);
  const updateProgress = useTaskStore((s) => s.updateProgress);
  const resetProgress = useTaskStore((s) => s.resetProgress);
  const disconnectSSE = useTaskStore((s) => s.disconnectSSE);
  const [images, setImages] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const requestSeq = useRef(0);
  const openLightbox = useLayoutStore((s) => s.openLightbox);

  // 加载任务详情（幂等：同一 id 重复触发只会各自完成，最后写入的是最新请求）
  useEffect(() => {
    const seq = ++requestSeq.current;
    setActiveTaskId(taskId);
    setLoading(true);
    setLoadError(null);
    resetProgress(taskId);

    // 已连接同一任务时保留已有进度，避免重挂载把流式内容清空
    const existing = useTaskStore.getState().progress[taskId];
    if (!existing) {
      updateProgress(taskId, { phase: "idle", message: "加载任务中…" });
    }

    getTask(taskId)
      .then(({ task, solution_content, image_urls }) => {
        if (seq !== requestSeq.current) return; // 用户已切到别的任务
        setImages(image_urls);
        if (task.status === "completed") {
          updateProgress(taskId, {
            phase: "done",
            message: "解答完成",
            answer: solution_content,
            filename: task.filename,
            timings: task.timings ?? null,
          });
        } else if (task.status === "failed") {
          updateProgress(taskId, {
            phase: "error",
            message: "任务失败",
            error: task.error_message || "未知错误",
          });
        } else if (task.status === "cancelled") {
          updateProgress(taskId, {
            phase: "cancelled",
            message: "任务已取消",
            answer: solution_content,
          });
        } else {
          // 仍在处理中：先给出大致阶段，随后由 SSE 覆盖
          updateProgress(taskId, {
            phase: task.status === "processing" ? "solving" : "classifying",
            message: task.status === "processing" ? "正在生成解答…" : "分类题目类型中…",
            answer: solution_content,
          });
        }
      })
      .catch((err: unknown) => {
        if (seq !== requestSeq.current) return;
        setLoadError(err instanceof Error ? err.message : "无法加载任务数据");
      })
      .finally(() => {
        if (seq !== requestSeq.current) return;
        setLoading(false);
      });

    return () => {
      // 离开任务页时断开该任务的流，避免后台连接堆积
      disconnectSSE(taskId);
    };
  }, [taskId, setActiveTaskId, updateProgress, resetProgress, disconnectSSE]);

  // 连接流：等详情加载完成后按实际状态决定是否需要流
  const phase = useTaskStore((s) => (taskId ? s.progress[taskId]?.phase : undefined));
  useEffect(() => {
    if (!taskId || loading || loadError) return;
    if (phase === undefined || phase === "done" || phase === "error" || phase === "cancelled") return;
    connectSSE(taskId, true);
  }, [taskId, loading, loadError, phase, connectSSE]);

  const backToNew = () => {
    disconnectSSE(taskId);
    setActiveTaskId(null);
    setImages([]);
    navigate("/");
  };

  if (loadError) {
    return (
      <div className="flex flex-col items-center justify-center gap-3 h-full text-center px-6">
        <p className="text-sm text-gray-600 font-medium">任务加载失败</p>
        <p className="text-xs text-gray-400">{loadError}</p>
        <button
          onClick={() => {
            setLoadError(null);
            // 触发重载：改变 ref 序号即可让 effect 重新执行
            requestSeq.current += 1;
            setLoading(true);
            getTask(taskId)
              .then(({ task, solution_content, image_urls }) => {
                setImages(image_urls);
                setLoadError(null);
                updateProgress(taskId, {
                  phase: task.status === "completed" ? "done" : isTerminalStatus(task.status) ? "error" : "solving",
                  answer: solution_content,
                  filename: task.filename,
                  error: task.error_message,
                });
              })
              .catch((err: unknown) => setLoadError(err instanceof Error ? err.message : "重试失败"))
              .finally(() => setLoading(false));
          }}
          className="mt-2 px-4 py-2 text-sm font-medium text-white bg-indigo-600 hover:bg-indigo-700 rounded-lg cursor-pointer"
        >
          重试
        </button>
        <button onClick={backToNew} className="text-xs text-indigo-500 hover:text-indigo-600 cursor-pointer">
          返回新建任务
        </button>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="flex flex-col items-center gap-3 text-gray-400">
          <svg className="animate-spin w-8 h-8" viewBox="0 0 24 24">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
          </svg>
          <p className="text-sm">加载历史任务…</p>
        </div>
      </div>
    );
  }

  if (images.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center gap-3 p-8 bg-gray-50 rounded-lg border border-gray-200 h-full text-center">
        <svg className="w-12 h-12 text-gray-300" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
            d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
        </svg>
        <p className="text-sm text-gray-500 font-medium">题目图片已清理</p>
        <p className="text-xs text-gray-400">解答内容仍可在右侧查看</p>
        <button onClick={backToNew} className="mt-2 text-xs text-indigo-500 hover:text-indigo-600 cursor-pointer">
          返回新建任务
        </button>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-2 h-full overflow-auto">
      <div className="flex items-center justify-between px-1">
        <span className="text-xs text-gray-400">{images.length} 张题目图片</span>
        <button onClick={backToNew} className="text-xs text-indigo-500 hover:text-indigo-600 cursor-pointer">
          返回新建任务
        </button>
      </div>
      {images.map((url, i) => (
        <button
          key={url}
          onClick={() => openLightbox(images, i)}
          className="block w-full cursor-zoom-in"
          title="点击查看大图"
        >
          <img
            src={url}
            alt={`题目图片 ${i + 1}`}
            className="w-full rounded-lg border border-gray-200 object-contain bg-gray-100"
          />
        </button>
      ))}
    </div>
  );
}

function NewTaskPage() {
  const navigate = useNavigate();
  const setActiveTaskId = useTaskStore((s) => s.setActiveTaskId);
  const resetProgress = useTaskStore((s) => s.resetProgress);
  const disconnectSSE = useTaskStore((s) => s.disconnectSSE);
  const [isProcessing, setIsProcessing] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const files = useUploadStore((s) => s.files);
  const clearFiles = useUploadStore((s) => s.clearFiles);

  const handleStart = useCallback(async () => {
    if (files.length === 0) return;

    const prevTaskId = useTaskStore.getState().activeTaskId;
    if (prevTaskId) disconnectSSE(prevTaskId);

    setIsProcessing(true);
    setUploadError(null);
    try {
      const { task_id } = await createTask(files.map((f) => f.file));
      resetProgress(task_id);
      setActiveTaskId(task_id);
      clearFiles();
      navigate(`/task/${task_id}`);
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : "创建任务失败，请重试");
    } finally {
      setIsProcessing(false);
    }
  }, [files, clearFiles, navigate, setActiveTaskId, resetProgress, disconnectSSE]);

  return (
    <div className="flex flex-col gap-3 h-full">
      {files.length === 0 ? (
        <UploadZone />
      ) : (
        <>
          <ImageViewer />
          <FilePreviewList />
        </>
      )}
      {uploadError && (
        <div className="px-3 py-2 rounded-lg bg-red-50 border border-red-200 text-xs text-red-600">
          {uploadError}
        </div>
      )}
      <UploadActions onStart={handleStart} loading={isProcessing} />
    </div>
  );
}

function MainPage() {
  const { taskId } = useParams<{ taskId: string }>();
  const connectGlobalSSE = useTaskStore((s) => s.connectGlobalSSE);
  const disconnectGlobalSSE = useTaskStore((s) => s.disconnectGlobalSSE);
  const setOnAutoImportedTask = useTaskStore((s) => s.setOnAutoImportedTask);
  const navigate = useNavigate();
  const [newTaskInfo, setNewTaskInfo] = useState<{ id: string; numImages: number } | null>(null);
  const [autoSwitch, setAutoSwitch] = useState(false);

  // 全局 SSE：监听自动导入与手机连接状态
  useEffect(() => {
    setOnAutoImportedTask((id: string, numImages: number) => {
      setNewTaskInfo({ id, numImages });
    });
    connectGlobalSSE();
    return () => {
      setOnAutoImportedTask(null);
      disconnectGlobalSSE();
    };
  }, [connectGlobalSSE, disconnectGlobalSSE, setOnAutoImportedTask]);

  // 预填充"已见过"的任务，避免刷新页面时把历史任务当成新导入
  useEffect(() => {
    listTasks(1)
      .then(({ tasks }) => {
        const seen = useTaskStore.getState().seenAutoImportedTasks;
        tasks.forEach((t) => seen.add(t.id));
      })
      .catch(() => {
        /* 忽略：仅用于去重 */
      });
  }, []);

  // 自动导入的新任务：默认只提示，不打断当前阅读（考试场景不该被抢屏）
  useEffect(() => {
    if (!newTaskInfo || !autoSwitch) return;
    navigate(`/task/${newTaskInfo.id}`);
    setNewTaskInfo(null);
  }, [newTaskInfo, autoSwitch, navigate]);

  const dismissNewTask = useCallback(() => setNewTaskInfo(null), []);

  return (
    <div className="h-[calc(100dvh-4rem)] flex flex-col">
      {newTaskInfo && (
        <div className="bg-indigo-50 border-b border-indigo-200 px-4 py-2 flex items-center justify-between gap-3">
          <div className="flex items-center gap-2 min-w-0">
            <span className="inline-flex h-2 w-2 rounded-full bg-indigo-400 animate-pulse shrink-0" />
            <span className="text-sm text-indigo-700 truncate">
              检测到新截图任务（{newTaskInfo.numImages} 张图片）
            </span>
          </div>
          <div className="flex items-center gap-3 shrink-0">
            <label className="flex items-center gap-1.5 text-xs text-indigo-600 cursor-pointer">
              <input
                type="checkbox"
                checked={autoSwitch}
                onChange={(e) => setAutoSwitch(e.target.checked)}
                className="cursor-pointer"
              />
              自动切换
            </label>
            <button
              onClick={() => navigate(`/task/${newTaskInfo.id}`)}
              className="text-xs font-medium text-indigo-600 hover:text-indigo-700 cursor-pointer"
            >
              查看
            </button>
            <button onClick={dismissNewTask} className="text-xs text-indigo-400 hover:text-indigo-500 cursor-pointer">
              忽略
            </button>
          </div>
        </div>
      )}

      <SplitPanelLayout
        left={
          <ErrorBoundary title="题目面板出错">
            {taskId ? <TaskPage key={taskId} taskId={taskId} /> : <NewTaskPage />}
          </ErrorBoundary>
        }
        right={
          <ErrorBoundary title="解答面板出错">
            <OutputPanel taskId={taskId ?? null} />
          </ErrorBoundary>
        }
      />
      <ImageLightbox />
    </div>
  );
}

/** 懒加载页面的统一占位。高度要减去顶栏，否则会出现多余的滚动条 */
const RouteFallback = ({ label }: { label: string }) => (
  <div className="h-[calc(100dvh-4rem)] flex items-center justify-center text-sm text-gray-400">{label}</div>
);

function SettingsRoute() {
  return (
    <ErrorBoundary title="设置页出错">
      <Suspense fallback={<RouteFallback label="加载设置…" />}>
        <SettingsPage />
      </Suspense>
    </ErrorBoundary>
  );
}

function LoginRoute() {
  return (
    <ErrorBoundary title="登录页出错">
      <Suspense fallback={<RouteFallback label="加载登录页…" />}>
        <LoginPage />
      </Suspense>
    </ErrorBoundary>
  );
}

function UsageRoute() {
  return (
    <ErrorBoundary title="用量页出错">
      <Suspense fallback={<RouteFallback label="加载用量…" />}>
        <UsagePage />
      </Suspense>
    </ErrorBoundary>
  );
}

function AdminRoute() {
  return (
    <RequireAdmin>
      <ErrorBoundary title="管理看板出错">
        <Suspense fallback={<RouteFallback label="加载看板…" />}>
          <AdminPage />
        </Suspense>
      </ErrorBoundary>
    </RequireAdmin>
  );
}

function HistoryPage() {
  const navigate = useNavigate();
  return (
    <div className="h-[calc(100dvh-4rem)] bg-white">
      <ErrorBoundary title="历史记录出错">
        <TaskHistoryPage onSelectTask={(id) => navigate(`/task/${id}`)} />
      </ErrorBoundary>
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
          <Route path="/task/:taskId" element={<MainPage />} />
          <Route path="/history" element={<HistoryPage />} />
          <Route path="/settings" element={<SettingsRoute />} />
          <Route path="/login" element={<LoginRoute />} />
          {/* 受保护路由：AUTH_ENABLED=false 时守卫会直接放行，不会强制登录 */}
          <Route
            path="/usage"
            element={
              <RequireAuth>
                <UsageRoute />
              </RequireAuth>
            }
          />
          <Route
            path="/admin"
            element={
              <RequireAuth>
                <AdminRoute />
              </RequireAuth>
            }
          />
          <Route path="*" element={<MainPage />} />
        </Routes>
      </div>
    </HashRouter>
  );
}
