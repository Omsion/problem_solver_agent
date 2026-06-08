import { useState, useEffect } from "react";
import { useTaskStore } from "../../stores/useTaskStore";
import { ProgressSteps } from "./ProgressSteps";
import { MarkdownRenderer } from "./MarkdownRenderer";
import { ThinkingBlock } from "./ThinkingBlock";

interface Props {
  taskId: string | null;
}

type Tab = "answer" | "thinking";

export const OutputPanel = ({ taskId }: Props) => {
  const progress = useTaskStore((s) => (taskId ? s.progress[taskId] : undefined));
  const [tab, setTab] = useState<Tab>("answer");

  useEffect(() => {
    if (progress?.phase === "done") setTab("answer");
  }, [progress?.phase]);

  if (!taskId || !progress) {
    return (
      <div className="flex items-center justify-center h-full text-gray-400 text-sm">
        选择一个任务查看解答
      </div>
    );
  }

  if (progress.phase === "idle") {
    return (
      <div className="flex items-center justify-center h-full text-gray-400 text-sm">
        等待任务开始...
      </div>
    );
  }

  if (progress.phase === "error") {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-3 p-6">
        <div className="bg-red-50 border border-red-200 rounded-xl p-6 max-w-md w-full text-center">
          <p className="text-red-600 font-medium text-sm">处理失败</p>
          <p className="text-red-500 text-xs mt-1">{progress.error || "未知错误"}</p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full">
      {/* Tab bar */}
      <div className="flex border-b border-gray-200 px-4">
        <button
          onClick={() => setTab("answer")}
          className={`px-4 py-2.5 text-sm font-medium transition-colors border-b-2 -mb-px cursor-pointer ${
            tab === "answer"
              ? "border-indigo-600 text-indigo-600"
              : "border-transparent text-gray-400 hover:text-gray-600"
          }`}
        >
          解答
        </button>
        {progress.thinking ? (
          <button
            onClick={() => setTab("thinking")}
            className={`px-4 py-2.5 text-sm font-medium transition-colors border-b-2 -mb-px cursor-pointer ${
              tab === "thinking"
                ? "border-indigo-600 text-indigo-600"
                : "border-transparent text-gray-400 hover:text-gray-600"
            }`}
          >
            思考过程
            <span className="ml-1.5 text-xs bg-indigo-100 text-indigo-600 px-1.5 py-0.5 rounded-full">
              {progress.thinking.length}
            </span>
          </button>
        ) : null}
      </div>

      {/* Tab content */}
      <div className="flex-1 overflow-auto">
        {tab === "answer" && (
          <div className="p-4">
            {progress.phase !== "done" && (
              <ProgressSteps phase={progress.phase} message={progress.message} />
            )}
            {progress.answer ? (
              <MarkdownRenderer content={progress.answer} />
            ) : progress.phase !== "done" ? (
              <div className="flex items-center justify-center py-12 text-gray-400 text-sm">
                <svg className="animate-spin w-5 h-5 mr-2" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                </svg>
                正在生成解答...
              </div>
            ) : null}
          </div>
        )}
        {tab === "thinking" && (
          <div className="p-4">
            <ThinkingBlock content={progress.thinking} />
          </div>
        )}
      </div>
    </div>
  );
};
