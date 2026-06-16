import type { ReactNode } from "react";
import { useState, useEffect } from "react";
import { useTaskStore } from "../../stores/useTaskStore";

interface Props {
  left: ReactNode;
  right: ReactNode;
  /** 当前是否在查看某个任务（来自路由 path 参数，不再依赖 query） */
  hasTask: boolean;
}

type Tab = "left" | "right";

export const MobileLayout = ({ left, right, hasTask }: Props) => {
  // 未在任务页时先看"解答"；进入任务详情后默认看题目，
  // 一旦开始出答案会自动切到解答（见下方 effect）。
  const [tab, setTab] = useState<Tab>(hasTask ? "left" : "right");
  const activeTaskId = useTaskStore((s) => s.activeTaskId);
  const phase = useTaskStore((s) => (activeTaskId ? s.progress[activeTaskId]?.phase : undefined));

  // 进入任务详情时先看题目
  useEffect(() => {
    if (hasTask) setTab("left");
  }, [hasTask]);

  // 有任务开始处理时自动切到解答页
  useEffect(() => {
    if (activeTaskId && phase && phase !== "idle" && phase !== "done" && phase !== "cancelled" && phase !== "error") {
      setTab("right");
    }
  }, [activeTaskId, phase]);

  const busy = !!phase && phase !== "idle" && phase !== "done" && phase !== "cancelled" && phase !== "error";

  return (
    <div className="flex flex-col h-full">
      <div className="flex-1 min-h-0 overflow-hidden">
        <div className={`h-full ${tab === "left" ? "" : "hidden"}`}>{left}</div>
        <div className={`h-full ${tab === "right" ? "" : "hidden"}`}>{right}</div>
      </div>

      <div className="border-t border-gray-200 bg-white safe-area-bottom shrink-0">
        <div className="flex items-center">
          <button
            onClick={() => setTab("left")}
            className={`flex-1 flex items-center justify-center gap-2 py-3 text-sm font-medium transition-colors cursor-pointer touch-target ${
              tab === "left" ? "text-indigo-600 bg-indigo-50" : "text-gray-500 hover:text-gray-700 hover:bg-gray-50"
            }`}
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8}
                d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z" />
            </svg>
            题目
          </button>
          <button
            onClick={() => setTab("right")}
            className={`flex-1 flex items-center justify-center gap-2 py-3 text-sm font-medium transition-colors cursor-pointer touch-target ${
              tab === "right" ? "text-indigo-600 bg-indigo-50" : "text-gray-500 hover:text-gray-700 hover:bg-gray-50"
            }`}
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8}
                d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
            </svg>
            解答
            {busy && <span className="w-2 h-2 rounded-full bg-indigo-500 animate-pulse" />}
            {phase === "done" && <span className="w-2 h-2 rounded-full bg-green-500" />}
            {phase === "error" && <span className="w-2 h-2 rounded-full bg-red-500" />}
          </button>
        </div>
      </div>
    </div>
  );
};
