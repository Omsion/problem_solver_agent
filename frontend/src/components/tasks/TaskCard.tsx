import type { Task } from "../../types";
import { formatTs, formatDuration } from "../../lib/utils";
import { statusLabel, isTerminalStatus } from "../../lib/taskStatus";
import { Badge } from "../ui/badge";
import { useIsMobile } from "../../hooks/useMediaQuery";

const statusVariant: Record<string, "default" | "info" | "success" | "error"> = {
  pending: "default",
  processing: "info",
  completed: "success",
  failed: "error",
  cancelled: "default",
};

interface Props {
  task: Task;
  /** 已本地化的题型名 */
  kindLabel: string;
  retrying?: boolean;
  onDelete: (id: string) => void;
  onClick: (id: string) => void;
  onRetry: (id: string) => void;
}

export const TaskCard = ({ task, kindLabel, retrying, onDelete, onClick, onRetry }: Props) => {
  const isMobile = useIsMobile();
  const canRetry = isTerminalStatus(task.status) && task.status !== "completed";

  return (
    <div
      onClick={() => onClick(task.id)}
      className="flex items-center gap-2 sm:gap-4 px-3 sm:px-4 h-14 bg-white border border-gray-100 rounded-lg hover:shadow-sm hover:border-gray-200 transition-all cursor-pointer"
    >
      <Badge variant={statusVariant[task.status] ?? "default"}>
        {task.status === "processing" && (
          <span className="inline-block w-1.5 h-1.5 rounded-full bg-current mr-1 animate-pulse" />
        )}
        {statusLabel(task.status)}
      </Badge>

      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium text-gray-900 truncate">{kindLabel}</p>
        <p className="text-xs text-gray-400 truncate">
          {task.filename || formatTs(task.created_at)}
        </p>
      </div>

      <div className="flex items-center gap-3 text-xs text-gray-400 shrink-0">
        <span>{task.num_images} 张图</span>
        {!isMobile && <span>{formatTs(task.created_at)}</span>}
        {task.timings?.total ? <span>{formatDuration(task.timings.total)}</span> : null}
      </div>

      {canRetry && (
        <button
          onClick={(e) => {
            e.stopPropagation();
            onRetry(task.id);
          }}
          disabled={retrying}
          className="text-xs text-indigo-500 hover:text-indigo-700 disabled:opacity-50 px-2 py-1 cursor-pointer shrink-0"
          title="重试该任务"
        >
          {retrying ? "重试中…" : "重试"}
        </button>
      )}

      <button
        onClick={(e) => {
          e.stopPropagation();
          onDelete(task.id);
        }}
        className="text-gray-300 hover:text-red-500 transition-colors p-1 min-w-[44px] min-h-[44px] flex items-center justify-center cursor-pointer shrink-0"
        title="删除"
      >
        <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
            d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
        </svg>
      </button>
    </div>
  );
};
