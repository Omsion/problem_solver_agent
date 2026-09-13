import { useState, useEffect, useMemo, useCallback, useRef } from "react";
import { RefreshCw, NotebookPen } from "lucide-react";
import { useTaskStore } from "../../stores/useTaskStore";
import { useIsMobile } from "../../hooks/useMediaQuery";
import { useWakeLock, vibrateReady } from "../../hooks/useWakeLock";
import { useElapsed } from "../../hooks/useElapsed";
import { cancelTask, retryTask, verifyTask, resolveTask } from "../../lib/api";
import { formatDuration } from "../../lib/utils";
import { fallbackAnswerCard, stripFrontmatter } from "../../lib/solutionText";
import { ProgressSteps } from "./ProgressSteps";
import { LazyAnswer, LazyReader, LazyTimings } from "./lazy";
import { ThinkingBlock } from "./ThinkingBlock";
import { AnswerCard } from "./AnswerCard";
import { ActionMenu } from "../ui/menu";
import { useConfirm } from "../ui/confirm";
import { notify } from "../ui/toast";
import type { AnswerCard as AnswerCardData } from "../../types";

interface Props {
  taskId: string | null;
}

type Tab = "answer" | "thinking";

const RUNNING_PHASES = new Set(["classifying", "ocr", "solving", "verifying", "archiving"]);

export const OutputPanel = ({ taskId }: Props) => {
  const progress = useTaskStore((s) => (taskId ? s.progress[taskId] : undefined));
  const updateProgress = useTaskStore((s) => s.updateProgress);
  const retryStream = useTaskStore((s) => s.retryStream);
  const isMobile = useIsMobile();
  const confirm = useConfirm();

  const [tab, setTab] = useState<Tab>("answer");
  const [showFull, setShowFull] = useState(false);
  const [isReadingMode, setIsReadingMode] = useState(false);
  const [copiedAll, setCopiedAll] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const phase = progress?.phase ?? "idle";
  const running = RUNNING_PHASES.has(phase);
  const finished = phase === "done" || phase === "cancelled";

  // 等待答案期间保持手机屏幕常亮（能力检测，不支持则静默降级）
  useWakeLock(running);

  // 答案就绪时震动提示一次
  const notifiedRef = useRef<string | null>(null);
  useEffect(() => {
    if (phase !== "done" || !taskId) return;
    if (notifiedRef.current === taskId) return;
    notifiedRef.current = taskId;
    vibrateReady(200);
  }, [phase, taskId]);

  useEffect(() => {
    if (phase === "done") setTab("answer");
  }, [phase]);

  // 完成时收起完整解答，让答案卡成为主角
  useEffect(() => {
    if (finished) setShowFull(false);
  }, [finished, taskId]);

  // 处理中的实时计时，让"是否还在跑"一眼可见。
  // 起点是任务的真实开始时间（startedAt），切换任务不会归零——见 useElapsed。
  const startedAt = progress?.startedAt ?? null;
  const elapsed = useElapsed(running, startedAt, taskId);

  const card = useMemo<AnswerCardData | null>(() => {
    if (!progress || !finished) return null;
    // 空卡片会把完整解答折叠起来，等于什么都看不到，所以按"没有卡"处理
    if (progress.answerCard?.text?.trim()) return progress.answerCard;
    if (progress.answer) {
      const fallback = fallbackAnswerCard(progress.answer);
      return fallback.text.trim() ? fallback : null;
    }
    return null;
  }, [progress, finished]);

  // 展示与复制都去掉文件头的 YAML 元信息：那是内部记账字段，不是答案内容
  const answerBody = useMemo(() => stripFrontmatter(progress?.answer ?? ""), [progress?.answer]);

  const handleCopyAll = useCallback(async () => {
    const text = answerBody;
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      const area = document.createElement("textarea");
      area.value = text;
      area.style.position = "fixed";
      area.style.opacity = "0";
      document.body.appendChild(area);
      area.select();
      try {
        document.execCommand("copy");
      } catch {
        /* ignore */
      }
      document.body.removeChild(area);
    }
    setCopiedAll(true);
    setTimeout(() => setCopiedAll(false), 2000);
  }, [answerBody]);

  const handleVerify = useCallback(async () => {
    if (!taskId) return;
    updateProgress(taskId, { verifying: true, verification: null });
    setActionError(null);
    try {
      const { verification } = await verifyTask(taskId);
      updateProgress(taskId, { verification, verifying: false });
      if (verification.verdict === "disagree") {
        notify.error("核对发现疑点", "请查看答案卡上方的提示");
      } else if (verification.verdict === "agree") {
        notify.success("核对通过");
      } else {
        notify.info("无法判定", verification.reason || "图片信息不足");
      }
    } catch (err) {
      updateProgress(taskId, { verifying: false });
      const message = err instanceof Error ? err.message : "核对失败";
      setActionError(message);
      notify.error("核对失败", message);
    }
  }, [taskId, updateProgress]);

  const handleResolve = useCallback(
    async (style?: "OPTIMAL" | "EXPLORATORY", thinking?: boolean) => {
      if (!taskId) return;
      setBusy(true);
      setActionError(null);
      try {
        await resolveTask(taskId, { style, thinking });
        updateProgress(taskId, {
          phase: "solving",
          message: style ? `正在用 ${style} 风格重新求解…` : "正在重新求解…",
          answer: "",
          answerCard: null,
          verification: null,
          error: null,
          // 新一轮求解：计时起点重算
          startedAt: Date.now(),
        });
        retryStream(taskId);
        notify.info("已开始重新求解", "复用已识别的题目文本，跳过识别步骤");
      } catch (err) {
        const message = err instanceof Error ? err.message : "重新求解失败";
        setActionError(message);
        notify.error("重新求解失败", message);
      } finally {
        setBusy(false);
      }
    },
    [taskId, updateProgress, retryStream],
  );

  const handleCancel = useCallback(async () => {
    if (!taskId) return;
    const ok = await confirm({
      title: "取消这个任务？",
      description: "已生成的内容会保留为部分解答，之后可以重试。",
      confirmLabel: "取消任务",
      cancelLabel: "继续处理",
      destructive: true,
    });
    if (!ok) return;

    setBusy(true);
    setActionError(null);
    try {
      await cancelTask(taskId);
    } catch (err) {
      const message = err instanceof Error ? err.message : "取消失败";
      setActionError(message);
      notify.error("取消失败", message);
    } finally {
      setBusy(false);
    }
  }, [taskId, confirm]);

  const handleRetry = useCallback(async () => {
    if (!taskId) return;
    setBusy(true);
    setActionError(null);
    try {
      await retryTask(taskId);
      updateProgress(taskId, { phase: "solving", message: "正在重试…", error: null, startedAt: Date.now() });
      retryStream(taskId);
    } catch (err) {
      const message = err instanceof Error ? err.message : "重试失败";
      setActionError(message);
      notify.error("重试失败", message);
    } finally {
      setBusy(false);
    }
  }, [taskId, retryStream, updateProgress]);

  const statusHint = useMemo(() => {
    const status = progress?.streamStatus;
    if (!status || finished || phase === "error") return null;
    if (status === "reconnecting") {
      return `连接中断，正在重连（第 ${progress?.reconnectAttempt ?? 1} 次）…`;
    }
    if (status === "failed") return "连接失败";
    return null;
  }, [progress?.streamStatus, progress?.reconnectAttempt, phase, finished]);

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
        <Spinner />
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
        <button
          onClick={handleRetry}
          disabled={busy}
          className="px-4 py-2 text-sm font-medium text-white bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 rounded-lg cursor-pointer"
        >
          {busy ? "重试中…" : "重试"}
        </button>
      </div>
    );
  }

  const showCard = finished && card !== null;
  const answerVisible = !showCard || showFull;

  return (
    <div className="flex flex-col h-full">
      {/* 工具栏 */}
      <div className="flex border-b border-gray-200 px-2 sm:px-4 items-center shrink-0">
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

        <div className="ml-auto flex items-center">
          {/* 运行状态与操作放在同一行：状态在左、操作在右，正文区只留内容 */}
          {running && (
            <span className="flex items-center gap-1.5 px-1 text-xs text-gray-400 tabular-nums whitespace-nowrap">
              <span className="hidden sm:inline">{answerBody.length} 字符</span>
              <span className="hidden text-gray-300 sm:inline">·</span>
              <span>已用时 {formatDuration(elapsed)}</span>
            </span>
          )}
          {progress.answer && (
            <button
              onClick={handleCopyAll}
              className="px-2 sm:px-3 py-2.5 text-sm font-medium text-gray-400 hover:text-indigo-600 transition-colors cursor-pointer touch-target"
              title="复制完整解答"
            >
              {copiedAll ? "已复制" : "复制全文"}
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
          {finished && (
            <ActionMenu
              trigger={<RefreshCw className="w-4 h-4" />}
              label="重新求解"
              items={[
                { label: "用最优解风格重解", onSelect: () => void handleResolve("OPTIMAL") },
                { label: "用讲解风格重解", onSelect: () => void handleResolve("EXPLORATORY") },
                { label: "开启思考模式重解", onSelect: () => void handleResolve(undefined, true) },
                { label: "关闭思考模式重解", onSelect: () => void handleResolve(undefined, false) },
              ]}
            />
          )}
          {running && (
            <button
              onClick={handleCancel}
              disabled={busy}
              className="px-2 sm:px-3 py-2.5 text-sm font-medium text-red-400 hover:text-red-600 disabled:opacity-50 transition-colors cursor-pointer touch-target"
              title="取消这个任务"
            >
              {isMobile ? "取消" : "取消任务"}
            </button>
          )}
        </div>
      </div>

      <div className="flex-1 overflow-auto">
        {tab === "answer" && (
          <div className="p-4 space-y-4">
            {running && <ProgressSteps phase={phase} message={progress.message} />}

            {statusHint && (
              <div className="flex items-center justify-between gap-2 px-3 py-2 rounded-lg bg-amber-50 border border-amber-200">
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
              <div className="px-3 py-2 rounded-lg bg-red-50 border border-red-200 text-xs text-red-600">
                {actionError}
              </div>
            )}

            {/* 答案卡：完成后的主角 */}
            {showCard && card && (
              <AnswerCard
                card={card}
                verification={progress.verification}
                verifying={progress.verifying}
                onVerify={() => void handleVerify()}
                onShowFull={() => setShowFull(true)}
                canVerify={Boolean(progress.answer)}
              />
            )}

            {running && answerBody && (
              <div className="rounded-xl border border-gray-200 bg-white p-4">
                <pre className="whitespace-pre-wrap break-words font-sans text-sm leading-relaxed text-gray-800 max-h-[55vh] overflow-auto">
                  {answerBody}
                </pre>
              </div>
            )}

            {running && !progress.answer && (
              <div className="flex items-center justify-center py-12 text-gray-400 text-sm gap-2">
                <Spinner />
                {progress.message || "正在生成解答…"}
              </div>
            )}

            {finished && progress.answer && answerVisible && (
              <div className="rounded-xl border border-gray-200 bg-white p-4">
                <div className="flex items-center gap-2 mb-3 text-xs text-gray-400">
                  <NotebookPen className="w-3.5 h-3.5" />
                  完整解答
                </div>
                <LazyAnswer content={answerBody} />
              </div>
            )}

            {finished && !progress.answer && (
              <p className="text-center text-sm text-gray-400 py-8">没有可显示的内容</p>
            )}

            <LazyTimings timings={progress.timings} />
          </div>
        )}

        {tab === "thinking" && (
          <div className="p-4">
            <ThinkingBlock content={progress.thinking} streaming={running} />
          </div>
        )}
      </div>

      {isReadingMode && progress.answer && (
        <LazyReader content={answerBody} onClose={() => setIsReadingMode(false)} />
      )}
    </div>
  );
};

const Spinner = () => (
  <svg className="animate-spin w-5 h-5" viewBox="0 0 24 24">
    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
  </svg>
);
