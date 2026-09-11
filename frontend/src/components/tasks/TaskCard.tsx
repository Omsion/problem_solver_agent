import { MoreHorizontal } from "lucide-react";
import type { Task } from "../../types";
import { formatTs, formatDuration } from "../../lib/utils";
import { statusLabel, isTerminalStatus } from "../../lib/taskStatus";
import { Badge } from "../ui/badge";
import { ActionMenu } from "../ui/menu";
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

export const TaskCard = ({ task, kindLabel, retrying, onDelete, onClick, onRetry }: Props) => {  const isMobile = useIsMobile();
  const canRetry = isTerminalStatus(task.status) && task.status !== "completed";

  return (
    <div
      onClick={() => onClick(task.id)}
      className="flex items-center gap-2 sm:gap-3 px-3 sm:px-4 h-14 bg-white border border-gray-100 rounded-lg hover:shadow-sm hover:border-gray-200 transition-all cursor-pointer"
    >
      <Badge variant={statusVariant[task.status] ?? "default"}>
        {task.status === "processing" && (
          <span className="inline-block w-1.5 h-1.5 rounded-full bg-current mr-1 animate-pulse" />
        )}
        {statusLabel(task.status)}
      </Badge>

      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium text-gray-900 truncate">{kindLabel}</p>
        <p className="text-xs text-gray-400 truncate">{task.filename || formatTs(task.created_at)}</p>
      </div>

      <div className="flex items-center gap-3 text-xs text-gray-400 shrink-0">
        <span>{task.num_images} 张图</span>
        {!isMobile && <span>{formatTs(task.created_at)}</span>}
        {task.timings?.total ? <span>{formatDuration(task.timings.total)}</span> : null}
      </div>

      {/* 多个操作收敛到一个菜单，避免小屏上图标按钮互相误触 */}
      <ActionMenu
        align="end"
        label="任务操作"
        trigger={<MoreHorizontal className="w-4 h-4" />}
        items={[
          {
            label: retrying ? "重试中…" : "重试",
            disabled: !canRetry || Boolean(retrying),
            onSelect: () => onRetry(task.id),
          },
          { label: "删除", destructive: true, onSelect: () => onDelete(task.id) },
        ]}
      />
    </div>
  );
};
