import { useState, useEffect, useMemo, useCallback } from "react";
import { useTaskStore } from "../../stores/useTaskStore";
import { useIsMobile } from "../../hooks/useMediaQuery";
import { cancelTask, retryTask } from "../../lib/api";
import { formatDuration } from "../../lib/utils";
import { ProgressSteps } from "./ProgressSteps";
import { LazyAnswer, LazyReader, LazyTimings } from "./lazy";
import { ThinkingBlock } from "./ThinkingBlock";

interface Props {
  taskId: string | null;
}

type Tab = "answer" | "thinking";

const RUNNING_PHASES = new Set(["classifying", "ocr", "solving", "verifying", "archiving"]);

export const OutputPanel = ({ taskId }: Props) => {
  const progress = useTaskStore((s) => (taskId ? s.progress[taskId] : undefined));
  const retryStream = useTaskStore((s) => s.retryStream);
  const isMobile = useIsMobile();
  const [tab, setTab] = useState<Tab>("answer");
  const [isReadingMode, setIsReadingMode] = useState(false);
  const [copied, setCopied] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [elapsed, setElapsed] = useState(0);

  const phase = progress?.phase ?? "idle";
  const running = RUNNING_PHASES.has(phase);

  useEffect(() => {
    if (phase === "done") setTab("answer");
  }, [phase]);

  // 处理中的实时计时，让"它到底还在不在跑"一眼可见
  useEffect(() => {
    if (!running) {
      setElapsed(0);
      return;
    }
    const startedAt = Date.now();
    setElapsed(0);
    const timer = setInterval(() => setElapsed(Date.now() - startedAt), 500);
    return () => clearInterval(timer);
  }, [running, taskId]);

  const handleCopy = useCallback(async () => {
    const text = progress?.answer ?? "";
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      try {
        document.execCommand("copy");
      } catch {
        /* ignore */
      }
      document.body.removeChild(ta);
    }
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }, [progress?.answer]);

  const handleCancel = useCallback(async () => {
    if (!taskId) return;
    setBusy(true);
    setActionError(null);
    try {
      await cancelTask(taskId);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "取消失败");
    } finally {
      setBusy(false);
    }
  }, [taskId]);

  const handleRetry = useCallback(async () => {
    if (!taskId) return;
    setBusy(true);
    setActionError(null);
    try {
      await retryTask(taskId);
      retryStream(taskId);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "重试失败");
    } finally {
      setBusy(false);
    }
  }, [taskId, retryStream]);

  const statusHint = useMemo(() => {
    const status = progress?.streamStatus;
    if (!status || phase === "done" || phase === "error" || phase === "cancelled") return null;
    if (status === "reconnecting") {
      return `连接中断，正在重连（第 ${progress?.reconnectAttempt ?? 1} 次）…`;
    }
    if (status === "failed") return "连接失败";
    return null;
  }, [progress?.streamStatus, progress?.reconnectAttempt, phase]);

  if (!taskId) {
    return (
      <div className="flex items-center justify-center h-full text-gray-400 text-sm">
        选择一个任务查看解答
      </div>
    );
  }

  if (!progress || (phase === "idle" && progress.message)) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-3 text-gray-400 text-sm">
        <svg className="animate-spin w-5 h-5" viewBox="0 0 24 24">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
        </svg>
        <span>{progress?.message || "加载任务中…"}</span>
      </div>
    );
  }

  if (phase === "idle") {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-3 text-gray-400 text-sm">
        <span>等待任务开始…</span>
        <button
          onClick={() => retryStream(taskId)}
          className="text-xs text-indigo-500 hover:text-indigo-600 cursor-pointer"
        >
          手动连接
        </button>
      </div>
    );
  }

  if (phase === "error") {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-3 p-6">
        <div className="bg-red-50 border border-red-200 rounded-xl p-6 max-w-md w-full text-center">
          <p className="text-red-600 font-medium text-sm">处理失败</p>
          <p className="text-red-500 text-xs mt-1 break-words">{progress.error || "未知错误"}</p>
        </div>
        {actionError && <p className="text-xs text-red-500">{actionError}</p>}
        <div className="flex items-center gap-3">
          <button
            onClick={handleRetry}
            disabled={busy}
            className="px-4 py-2 text-sm font-medium text-white bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 rounded-lg cursor-pointer"
          >
            {busy ? "重试中…" : "重试"}
          </button>
        </div>
      </div>
    );
  }

  if (phase === "cancelled") {
    return (
      <div className="flex flex-col h-full">
        <div className="p-4">
          <div className="bg-gray-50 border border-gray-200 rounded-xl p-4 text-center">
            <p className="text-gray-700 font-medium text-sm">任务已取消</p>
            <p className="text-gray-500 text-xs mt-1">已保留取消前生成的内容</p>
          </div>
        </div>
        <div className="flex-1 overflow-auto px-4">
          {progress.answer ? (
            <LazyAnswer content={progress.answer} />
          ) : (
            <p className="text-center text-sm text-gray-400 py-8">没有可显示的内容</p>
          )}
        </div>
        <div className="p-4 border-t border-gray-200 flex justify-center">
          <button
            onClick={handleRetry}
            disabled={busy}
            className="px-4 py-2 text-sm font-medium text-white bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 rounded-lg cursor-pointer"
          >
            {busy ? "重试中…" : "重试"}
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full">
      <div className="flex border-b border-gray-200 px-2 sm:px-4 items-center">
        <button
          onClick={() => setTab("answer")}
          className={`px-3 sm:px-4 py-2.5 text-sm font-medium transition-colors border-b-2 -mb-px cursor-pointer ${
            tab === "answer" ? "border-indigo-600 text-indigo-600" : "border-transparent text-gray-400 hover:text-gray-600"
          }`}
        >
          解答
        </button>
        {progress.thinking ? (
          <button
            onClick={() => setTab("thinking")}
            className={`px-3 sm:px-4 py-2.5 text-sm font-medium transition-colors border-b-2 -mb-px cursor-pointer ${
              tab === "thinking" ? "border-indigo-600 text-indigo-600" : "border-transparent text-gray-400 hover:text-gray-600"
            }`}
          >
            思考过程
            <span className="ml-1.5 text-xs bg-indigo-100 text-indigo-600 px-1.5 py-0.5 rounded-full">
              {progress.thinking.length}
            </span>
          </button>
        ) : null}

        <div className="ml-auto flex items-center gap-1">
          {progress.answer && (
            <button
              onClick={handleCopy}
              className="px-2 sm:px-3 py-2.5 text-sm font-medium text-gray-400 hover:text-indigo-600 transition-colors cursor-pointer touch-target"
              title="复制完整解答"
            >
              {copied ? "已复制" : "复制"}
            </button>
          )}
          {progress.answer && (
            <button
              onClick={() => setIsReadingMode(true)}
              className="px-2 sm:px-3 py-2.5 text-sm font-medium text-gray-400 hover:text-indigo-600 transition-colors cursor-pointer touch-target"
              title="阅读模式"
            >
              {isMobile ? "阅读" : "阅读模式"}
            </button>
          )}
        </div>
      </div>

      <div className="flex-1 overflow-auto">
        {tab === "answer" && (
          <div className="p-4">
            {running && <ProgressSteps phase={phase} message={progress.message} />}

            {running && (
              <div className="flex items-center justify-between text-xs text-gray-400 mt-1 mb-3 px-1">
                <span>已用时 {formatDuration(elapsed)}</span>
                <span>{progress.answer.length} 字符</span>
                <button
                  onClick={handleCancel}
                  disabled={busy}
                  className="text-red-400 hover:text-red-600 disabled:opacity-50 cursor-pointer"
                >
                  取消任务
                </button>
              </div>
            )}

            {statusHint && (
              <div className="flex items-center justify-between gap-2 mb-3 px-3 py-2 rounded-lg bg-amber-50 border border-amber-200">
                <span className="text-xs text-amber-700">{statusHint}</span>
                <button
                  onClick={() => retryStream(taskId)}
                  className="text-xs font-medium text-amber-700 hover:text-amber-900 cursor-pointer"
                >
                  立即重试
                </button>
              </div>
            )}

            {actionError && (
              <div className="mb-3 px-3 py-2 rounded-lg bg-red-50 border border-red-200 text-xs text-red-600">
                {actionError}
              </div>
            )}

            {progress.answer ? (
              <LazyAnswer content={progress.answer} />
            ) : running ? (
              <div className="flex items-center justify-center py-12 text-gray-400 text-sm">
                <svg className="animate-spin w-5 h-5 mr-2" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                </svg>
                {progress.message || "正在生成解答…"}
              </div>
            ) : null}

            <LazyTimings timings={progress.timings} />
          </div>
        )}

        {tab === "thinking" && (
          <div className="p-4">
            <ThinkingBlock content={progress.thinking} />
          </div>
        )}
      </div>

      {isReadingMode && progress.answer && (
        <LazyReader content={progress.answer} onClose={() => setIsReadingMode(false)} />
      )}
    </div>
  );
};
