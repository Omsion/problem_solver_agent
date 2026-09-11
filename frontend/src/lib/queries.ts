import { useQuery } from "@tanstack/react-query";
import { getHealth, getStats, getSystemStatus, listTasks } from "../lib/api";

/**
 * 服务端数据查询封装。
 *
 * 统一走 TanStack Query，拿到缓存、去重、窗口聚焦重新拉取与轮询能力，
 * 页面组件不再自己维护 loading/error/轮询定时器。
 */

/** 系统运行状态：轮询 10 秒，窗口重新聚焦时立即刷新 */
export function useSystemStatus() {
  return useQuery({
    queryKey: ["system-status"],
    queryFn: getSystemStatus,
    refetchInterval: 10_000,
    staleTime: 5_000,
  });
}

/** 后端健康与配置探测：很少变化，缓存久一点 */
export function useHealth() {
  return useQuery({
    queryKey: ["health"],
    queryFn: getHealth,
    staleTime: 60_000,
  });
}

/** 阶段耗时统计：按需刷新 */
export function useStats(limit = 50) {
  return useQuery({
    queryKey: ["stats", limit],
    queryFn: () => getStats(limit),
    staleTime: 30_000,
  });
}

/**
 * 任务列表。
 *
 * 有任务在处理中时按 3 秒轮询，全部终态后自动停止轮询——
 * 这正是原来在 TaskHistoryPage 里手写定时器想做的事。
 */
export function useTaskList(limit = 100) {
  return useQuery({
    queryKey: ["tasks", limit],
    queryFn: () => listTasks(limit),
    refetchInterval: (query) => {
      const tasks = query.state.data?.tasks ?? [];
      const hasActive = tasks.some((t) => t.status === "pending" || t.status === "processing");
      return hasActive ? 3_000 : false;
    },
  });
}
