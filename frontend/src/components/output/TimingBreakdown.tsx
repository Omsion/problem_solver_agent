import { useState } from "react";
import type { StageTimings } from "../../types";
import { formatDuration } from "../../lib/utils";

interface Props {
  timings: StageTimings | null | undefined;
}

const STAGE_LABELS: Array<[keyof StageTimings, string]> = [
  ["classify", "问题分类"],
  ["ocr", "文字识别"],
  ["polish", "文本润色"],
  ["solve", "AI 求解"],
  ["total", "总耗时"],
];

/**
 * 阶段耗时明细。默认折叠，展开后可以看清"到底慢在哪"，
 * 以及哪些阶段命中了缓存（重试时省下的时间）。
 */
export const TimingBreakdown = ({ timings }: Props) => {
  const [open, setOpen] = useState(false);

  if (!timings) return null;
  const rows = STAGE_LABELS.filter(([key]) => typeof timings[key] === "number" && (timings[key] as number) > 0);
  if (rows.length === 0) return null;

  const cached = new Set(timings.cached ?? []);

  return (
    <div className="mt-6 border border-gray-200 rounded-lg overflow-hidden">
      <button
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center gap-2 px-3 py-2 bg-gray-50 hover:bg-gray-100 transition-colors text-left cursor-pointer"
      >
        <svg
          className={`w-4 h-4 text-gray-400 transition-transform ${open ? "rotate-90" : ""}`}
          fill="none" stroke="currentColor" viewBox="0 0 24 24"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
        </svg>
        <span className="text-sm font-medium text-gray-600">阶段耗时</span>
        <span className="text-xs text-gray-400 ml-auto">{formatDuration(timings.total)}</span>
      </button>
      {open && (
        <div className="px-3 py-2 border-t border-gray-100 space-y-1">
          {rows.map(([key, label]) => (
            <div key={String(key)} className="flex items-center justify-between text-xs">
              <span className="text-gray-500 flex items-center gap-1.5">
                {label}
                {key !== "total" && cached.has(String(key)) && (
                  <span className="px-1.5 py-0.5 rounded bg-green-50 text-green-600 text-[10px]">缓存命中</span>
                )}
              </span>
              <span className="text-gray-700 tabular-nums">{formatDuration(timings[key] as number)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};
