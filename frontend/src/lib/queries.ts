import { useQuery } from "@tanstack/react-query";
import {
  getAdminDashboard,
  getAdminUser,
  getAdminUsers,
  getHealth,
  getStats,
  getSystemStatus,
  listTasks,
} from "../lib/api";

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

// ---- 认证与管理员 ----

/**
 * 当前用户 + 用量 + `auth_enabled`。
 *
 * 实现放在 `hooks/useAuth`（路由守卫与顶栏都要用它的派生状态），
 * 这里统一从 `lib/queries` 出口，页面只需要认一个模块。
 */
export { ME_QUERY_KEY, useAuth, useMe } from "../hooks/useAuth";

/** 管理员看板总览：用户数/调用/花费变化慢，缓存久一点 */
export function useAdminDashboard() {
  return useQuery({
    queryKey: ["admin", "dashboard"],
    queryFn: getAdminDashboard,
    staleTime: 30_000,
  });
}

/**
 * 管理员用户列表。
 *
 * 改额度/改角色后由调用方 `invalidateQueries(["admin"])`，
 * 因此这里不需要轮询。
 */
export function useAdminUsers(skip = 0, limit = 50) {
  return useQuery({
    queryKey: ["admin", "users", skip, limit],
    queryFn: () => getAdminUsers(skip, limit),
    staleTime: 30_000,
  });
}

/** 单个用户详情 + 用量流水 */
export function useAdminUser(userId: string | null) {
  return useQuery({
    queryKey: ["admin", "user", userId],
    queryFn: () => getAdminUser(userId as string),
    enabled: !!userId,
    staleTime: 30_000,
  });
}
