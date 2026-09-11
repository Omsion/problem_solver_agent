import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { deleteTask, retryTask } from "../../lib/api";
import { taskKindLabel } from "../../lib/problems";
import { useTaskList } from "../../lib/queries";
import { TaskCard } from "./TaskCard";
import { Button } from "../ui/button";
import { useConfirm } from "../ui/confirm";
import { notify } from "../ui/toast";

interface Props {
  onSelectTask: (taskId: string) => void;
}

/**
 * 任务列表。
 *
 * 数据获取、缓存与"有任务处理中时轮询、全部终态后停止轮询"的行为
 * 由 `useTaskList()`（TanStack Query）负责，这里只处理用户操作。
 */
export const TaskHistoryPage = ({ onSelectTask }: Props) => {
  const confirm = useConfirm();
  const queryClient = useQueryClient();
  const { data, isLoading, error, refetch, isFetching } = useTaskList();
  const [busyId, setBusyId] = useState<string | null>(null);

  const tasks = data?.tasks ?? [];
  const errorText = error instanceof Error ? error.message : error ? "加载失败，请重试" : null;

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["tasks"] });

  const handleDelete = async (id: string) => {
    const ok = await confirm({
      title: "删除这个任务？",
      description: "解答文件、上传原图与缓存都会被一并删除，无法恢复。",
      confirmLabel: "删除",
      destructive: true,
    });
    if (!ok) return;

    try {
      await deleteTask(id);
      notify.success("已删除");
      await invalidate();
    } catch (err) {
      notify.error("删除失败", err instanceof Error ? err.message : undefined);
    }
  };

  const handleRetry = async (id: string) => {
    setBusyId(id);
    try {
      await retryTask(id);
      notify.info("已开始重试", "复用已识别的题目文本");
      await invalidate();
      onSelectTask(id);
    } catch (err) {
      notify.error("重试失败", err instanceof Error ? err.message : undefined);
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div className="h-full flex flex-col min-h-0">
      <div className="flex items-center justify-between px-4 sm:px-6 py-4 border-b border-gray-200 shrink-0">
        <div className="flex items-center gap-2">
          <h2 className="text-base font-semibold text-gray-900">历史任务</h2>
          {!isLoading && (
            <span className="text-xs bg-gray-100 text-gray-500 px-2 py-0.5 rounded-full">{tasks.length}</span>
          )}
        </div>
        <Button variant="secondary" size="sm" onClick={() => void refetch()} disabled={isFetching}>
          {isFetching ? "刷新中…" : "刷新"}
        </Button>
      </div>

      <div className="flex-1 overflow-auto px-4 py-4 min-h-0">
        {isLoading && tasks.length === 0 && (
          <div className="space-y-2">
            {Array.from({ length: 5 }).map((_, i) => (
              <div key={i} className="h-14 bg-gray-100 rounded-lg animate-pulse" />
            ))}
          </div>
        )}

        {errorText && (
          <div className="flex flex-col items-center justify-center gap-3 py-8">
            <p className="text-sm text-red-500">{errorText}</p>
            <Button variant="secondary" size="sm" onClick={() => void refetch()}>
              重试
            </Button>
          </div>
        )}

        {!isLoading && !errorText && tasks.length === 0 && (
          <div className="flex flex-col items-center justify-center gap-2 py-16 text-gray-400">
            <svg className="w-12 h-12" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1}
                d="M20 13V6a2 2 0 00-2-2H6a2 2 0 00-2 2v7m16 0v5a2 2 0 01-2 2H6a2 2 0 01-2-2v-5m16 0h-2.586a1 1 0 00-.707.293l-2.414 2.414a1 1 0 01-.707.293h-3.172a1 1 0 01-.707-.293l-2.414-2.414A1 1 0 006.586 13H4" />
            </svg>
            <p className="text-sm">暂无历史任务</p>
            <p className="text-xs text-gray-400">截图或上传题目后，任务会出现在这里</p>
          </div>
        )}

        {tasks.length > 0 && (
          <div className="space-y-1.5">
            {tasks.map((task) => (
              <TaskCard
                key={task.id}
                task={task}
                kindLabel={taskKindLabel(task)}
                retrying={busyId === task.id}
                onDelete={handleDelete}
                onClick={onSelectTask}
                onRetry={handleRetry}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
};
