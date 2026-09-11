import { Link, useLocation, useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { QrCodeButton } from "./QrCodeButton";
import { useIsMobile } from "../../hooks/useMediaQuery";
import { useAuth } from "../../hooks/useAuth";
import { clearToken, isLoggedIn } from "../../lib/auth";
import { notify } from "../ui/toast";

export const AppHeader = () => {
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const isMobile = useIsMobile();
  const { user, authEnabled } = useAuth();

  // 后端没开认证时（AUTH_ENABLED=false）不存在"登录态"，此时不显示用量/退出，
  // 但 /usage 仍然可以访问——它展示的是本地用户的用量。
  const showAccount = authEnabled === true;
  const loggedIn = showAccount && (!!user || isLoggedIn());

  const linkClass = (path: string) =>
    `px-3 py-1.5 text-sm font-medium rounded-md transition-colors ${
      pathname === path
        ? "text-indigo-600 bg-indigo-50"
        : "text-gray-500 hover:text-gray-700 hover:bg-gray-100"
    }`;

  const handleLogout = () => {
    clearToken();
    // 清掉缓存里的身份，否则路由守卫还会拿着旧 user 放行
    queryClient.clear();
    notify.info("已退出登录");
    navigate("/", { replace: true });
  };

  return (
    <header
      className={`sticky top-0 z-10 h-16 bg-white border-b border-gray-200 shadow-sm flex items-center justify-between gap-2 shrink-0 ${
        isMobile ? "px-3" : "px-6"
      }`}
    >
      <div className="flex items-center gap-2 sm:gap-3 min-w-0">
        <div className="w-7 h-7 rounded-lg bg-indigo-600 flex items-center justify-center shrink-0">
          <svg className="w-4 h-4 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
              d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
          </svg>
        </div>
        <h1 className={`text-sm font-bold text-indigo-600 tracking-tight truncate ${isMobile ? "hidden" : ""}`}>
          自动化解题 Agent
        </h1>
      </div>

      <nav className="flex items-center gap-1 min-w-0 overflow-x-auto">
        <Link to="/" className={linkClass("/")}>
          解题台
        </Link>
        <Link to="/history" className={linkClass("/history")}>
          任务
        </Link>
        <Link to="/settings" className={linkClass("/settings")}>
          设置
        </Link>

        {/* 用量入口：本地模式也能看（后端会给内置本地用户的用量） */}
        <Link to="/usage" className={linkClass("/usage")}>
          用量
        </Link>

        {user?.role === "admin" && (
          <Link to="/admin" className={linkClass("/admin")}>
            管理
          </Link>
        )}

        {showAccount &&
          (loggedIn ? (
            <button
              onClick={handleLogout}
              className="px-3 py-1.5 text-sm font-medium rounded-md text-gray-500 hover:text-red-600 hover:bg-red-50 transition-colors cursor-pointer whitespace-nowrap"
            >
              退出
            </button>
          ) : (
            <Link to="/login" className={linkClass("/login")}>
              登录
            </Link>
          ))}

        <QrCodeButton />
      </nav>
    </header>
  );
};
