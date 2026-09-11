import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { useAuth } from "../../hooks/useAuth";

/**
 * 认证路由守卫。
 *
 * 唯一的判断依据是后端返回的 `auth_enabled`（经 `useAuth()` 暴露）：
 * - `AUTH_ENABLED=false`（默认，单用户本地模式）——直接放行，**绝不跳登录页**；
 * - `AUTH_ENABLED=true` 且未登录——带 `from` 跳到 `/login`；
 * - 还没探测出 `auth_enabled`——先渲染占位，避免"首帧误判 → 闪一下登录页 → 又跳回来"。
 *
 * `fallback` 兜底的时机是"探测失败"（后端不可达等），默认**照旧渲染页面**：
 * 本地单用户模式不该因为一次 `/me` 失败就白屏，真需要登录时后端会用 401
 * 让各页面自己的错误提示来接管。
 */
export const RequireAuth = ({ children, fallback }: { children: ReactNode; fallback?: ReactNode }) => {
  const { user, authEnabled, isLoading } = useAuth();
  const location = useLocation();

  if (authEnabled === null) {
    // 探测未完成：可以短时无内容，但不能替用户做"未登录"的决定
    if (isLoading) return null;
    return <>{fallback ?? children}</>;
  }
  if (!authEnabled) return <>{children}</>;
  if (user) return <>{children}</>;

  return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />;
};

/**
 * 管理员守卫。
 *
 * 非 admin 时渲染`拒绝`提示而不是重定向：用户明确点了"管理"入口，
 * 静默把他弹回首页会让人以为按钮坏了。
 */
export const RequireAdmin = ({ children }: { children: ReactNode }) => {
  const { user, authEnabled, isLoading } = useAuth();

  if (authEnabled === null && isLoading) return null;

  // 本地模式（auth_enabled=false）下后端同样按本地用户校验角色，
  // 这里沿用同一个判断，不做额外放宽。
  if (!user || user.role !== "admin") {
    return (
      <div className="h-[calc(100dvh-4rem)] flex flex-col items-center justify-center gap-2 px-6 text-center bg-gray-50">
        <svg className="w-12 h-12 text-gray-300" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={1.5}
            d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z"
          />
        </svg>
        <p className="text-sm font-medium text-gray-700">需要管理员权限</p>
        <p className="text-xs text-gray-400">当前账号不是管理员，无法查看用户与用量看板</p>
      </div>
    );
  }

  return <>{children}</>;
};
