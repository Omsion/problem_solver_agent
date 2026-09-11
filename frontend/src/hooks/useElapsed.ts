import { useEffect, useRef, useState } from "react";

/**
 * 「已用时」计时（毫秒）。
 *
 * 关键约定：**起点是任务的真实开始时间**（`startedAt`，来自任务的 `created_at`），
 * 而不是组件的挂载时刻。早先的实现在切换任务时用 `Date.now()` 重新起算，
 * 于是来回切换历史任务会让"已用时"归零，显示的数字和任务实际跑了多久无关。
 *
 * 拿不到 `startedAt` 时（例如任务刚开始、详情还没加载回来）退化为
 * "第一次观察到它在运行"的时刻，并在 `resetKey` 变化时重新取起点。
 *
 * @param running  任务是否仍在处理中；false 时立即归零（终态不显示计时）
 * @param startedAt 任务真实开始时间（毫秒时间戳）
 * @param resetKey 切换任务的标识（通常是 taskId），用于在缺省起点时区分不同任务
 */
export function useElapsed(
  running: boolean,
  startedAt: number | null | undefined,
  resetKey?: string | null,
): number {
  const [elapsed, setElapsed] = useState(0);
  const fallbackStartRef = useRef<number | null>(null);

  useEffect(() => {
    if (!running || startedAt) {
      fallbackStartRef.current = null;
      return;
    }
    fallbackStartRef.current = Date.now();
  }, [running, startedAt, resetKey]);

  useEffect(() => {
    if (!running) {
      setElapsed(0);
      return;
    }
    const tick = () => {
      const origin = startedAt ?? fallbackStartRef.current;
      if (origin === null) return;
      setElapsed(Math.max(0, Date.now() - origin));
    };
    tick();
    const timer = setInterval(tick, 500);
    return () => clearInterval(timer);
  }, [running, startedAt, resetKey]);

  return elapsed;
}
