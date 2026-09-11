import { useQuery } from "@tanstack/react-query";
import { ApiError, getMe } from "../lib/api";
import type { AuthUser, MeResponse, UsageSummary } from "../types";

/**
 * 当前登录用户与认证开关的统一入口。
 *
 * 为什么用 TanStack Query 而不是 React Context：
 * `/auth/me` 是**服务端数据**，本身就需要缓存、去重与失效。顶栏、用量页、
 * 路由守卫可能在同一帧内各调一次 `useAuth()`，交给 Query 的 dedupe 只会发
 * 一个请求；登录/退出时用 `invalidateQueries(["auth","me"])` 就能让所有订阅者
 * 一起刷新，不必再维护一个 Provider 与手写的 refetch 订阅。
 */

export const ME_QUERY_KEY = ["auth", "me"] as const;

export interface AuthState {
  /** 生效中的用户，未登录时为 null */
  user: AuthUser | null;
  usage: UsageSummary | null;
  /**
   * 后端 `AUTH_ENABLED` 开关。
   * `null` 表示还没探测出来——此时**不能**判定未登录，否则本地单用户模式
   * 会在首帧被误判成"需要登录"而弹登录页。
   */
  authEnabled: boolean | null;
  isAdmin: boolean;
  /** 首次探测中（含恢复本地模式身份） */
  isLoading: boolean;
  error: Error | null;
  refetch: () => void;
}

/**
 * `/auth/me` 查询。
 *
 * 关键点：`auth_enabled=false` 的本地模式下**同样要请求** `/me`——后端会把
 * 请求视为内置本地用户，前端借此拿到 `auth_enabled` 与用户信息，从而不会
 * 在不需要登录时强制跳转登录页。
 */
export function useMe() {
  return useQuery<MeResponse, Error>({
    queryKey: ME_QUERY_KEY,
    queryFn: getMe,
    // 令牌无效（401）重试没有意义，只会拖慢跳转登录的时机
    retry: (failureCount, error) => {
      if (error instanceof ApiError && error.status >= 400 && error.status < 500) return false;
      return failureCount < 1;
    },
    staleTime: 30_000,
  });
}

/** 读取当前用户与 `auth_enabled`，供路由守卫与顶栏复用 */
export function useAuth(): AuthState {
  const { data, isLoading, error, refetch } = useMe();

  return {
    user: data?.user ?? null,
    usage: data?.usage ?? null,
    authEnabled: data?.auth_enabled ?? null,
    isAdmin: data?.user?.role === "admin",
    isLoading,
    error: error ?? null,
    refetch: () => void refetch(),
  };
}

/**
 * 站内路径透传白名单：只接受以单个 `/` 开头的相对路径。
 *
 * `location.state.from` 是路由守卫塞进来的，但 state 也能被任何脚本伪造，
 * 直接 `navigate(from)` 会变成开放重定向，因此这里做一次白名单校验。
 */
export function safeRedirectPath(from: unknown, fallback = "/"): string {
  if (typeof from !== "string") return fallback;
  if (!from.startsWith("/") || from.startsWith("//")) return fallback;
  return from;
}
