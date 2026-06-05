import { useEffect, useState } from "react";
import type { Task } from "../../types";
import { listTasks, deleteTask } from "../../api/client";
import { TaskCard } from "./TaskCard";
import { Button } from "../ui/button";

interface Props {
  onSelectTask: (taskId: string) => void;
}

export const TaskHistoryPage = ({ onSelectTask }: Props) => {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchTasks = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await listTasks();
      setTasks(res.tasks);
    } catch {
      setError("加载失败，请重试");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchTasks();
  }, []);

  const handleDelete = async (id: string) => {
    if (!confirm("确认删除此任务？")) return;
    try {
      await deleteTask(id);
      setTasks((prev) => prev.filter((t) => t.id !== id));
    } catch {
      alert("删除失败");
    }
  };

  return (
    <div className="h-full flex flex-col">
      <div className="flex items-center justify-between px-6 py-4 border-b border-gray-200">
        <div className="flex items-center gap-2">
          <h2 className="text-base font-semibold text-gray-900">历史任务</h2>
          {!loading && (
            <span className="text-xs bg-gray-100 text-gray-500 px-2 py-0.5 rounded-full">
              {tasks.length}
            </span>
          )}
        </div>
      </div>

      <div className="flex-1 overflow-auto p-4">
        {loading && (
          <div className="space-y-2">
            {Array.from({ length: 5 }).map((_, i) => (
              <div
                key={i}
                className="h-14 bg-gray-100 rounded-lg animate-pulse"
              />
            ))}
          </div>
        )}

        {error && (
          <div className="flex flex-col items-center justify-center gap-3 py-12">
            <p className="text-sm text-gray-500">{error}</p>
            <Button variant="secondary" size="sm" onClick={fetchTasks}>
              重试
            </Button>
          </div>
        )}

        {!loading && !error && tasks.length === 0 && (
          <div className="flex flex-col items-center justify-center gap-2 py-16 text-gray-400">
            <svg className="w-12 h-12" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1}
                d="M20 13V6a2 2 0 00-2-2H6a2 2 0 00-2 2v7m16 0v5a2 2 0 01-2 2H6a2 2 0 01-2-2v-5m16 0h-2.586a1 1 0 00-.707.293l-2.414 2.414a1 1 0 01-.707.293h-3.172a1 1 0 01-.707-.293l-2.414-2.414A1 1 0 006.586 13H4" />
            </svg>
            <p className="text-sm">暂无历史任务</p>
          </div>
        )}

        {!loading && !error && tasks.length > 0 && (
          <div className="space-y-1.5">
            {tasks.map((task) => (
              <TaskCard
                key={task.id}
                task={task}
                onDelete={handleDelete}
                onClick={onSelectTask}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
};
