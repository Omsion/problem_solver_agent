import type { Task } from "../../types";
import { formatTs, statusLabel } from "../../lib/utils";
import { Badge } from "../ui/badge";

const statusVariant: Record<string, "default" | "info" | "success" | "error"> = {
  pending: "default",
  processing: "info",
  completed: "success",
  failed: "error",
};

interface Props {
  task: Task;
  onDelete: (id: string) => void;
  onClick: (id: string) => void;
}

export const TaskCard = ({ task, onDelete, onClick }: Props) => {
  return (
    <div
      onClick={() => onClick(task.id)}
      className="flex items-center gap-4 px-4 h-14 bg-white border border-gray-100 rounded-lg hover:shadow-sm hover:border-gray-200 transition-all cursor-pointer"
    >
      <Badge variant={statusVariant[task.status] ?? "default"}>
        {task.status === "processing" && (
          <span className="inline-block w-1.5 h-1.5 rounded-full bg-current mr-1 animate-pulse" />
        )}
        {statusLabel(task.status)}
      </Badge>

      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium text-gray-900 truncate">
          {task.problem_type || "未知题型"}
        </p>
        {task.filename && (
          <p className="text-xs text-gray-400 truncate">{task.filename}</p>
        )}
      </div>

      <div className="flex items-center gap-3 text-xs text-gray-400 shrink-0">
        <span>{task.num_images} 张图</span>
        <span>{formatTs(task.created_at)}</span>
      </div>

      <button
        onClick={(e) => {
          e.stopPropagation();
          onDelete(task.id);
        }}
        className="text-gray-300 hover:text-red-500 transition-colors p-1 cursor-pointer"
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
