import { Link, useLocation } from "react-router-dom";
import { QrCodeButton } from "./QrCodeButton";

export const AppHeader = () => {
  const { pathname } = useLocation();

  const linkClass = (path: string) =>
    `px-3 py-1.5 text-sm font-medium rounded-md transition-colors ${
      pathname === path
        ? "text-indigo-600 bg-indigo-50"
        : "text-gray-500 hover:text-gray-700 hover:bg-gray-100"
    }`;

  return (
    <header className="sticky top-0 z-10 h-14 bg-white border-b border-gray-200 shadow-sm flex items-center justify-between px-6 shrink-0">
      <div className="flex items-center gap-3">
        <div className="w-7 h-7 rounded-lg bg-indigo-600 flex items-center justify-center">
          <svg className="w-4 h-4 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
              d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
          </svg>
        </div>
        <h1 className="text-sm font-bold text-indigo-600 tracking-tight">
          自动化解题 Agent
        </h1>
      </div>

      <nav className="flex items-center gap-1">
        <Link to="/" className={linkClass("/")}>
          首页
        </Link>
        <Link to="/history" className={linkClass("/history")}>
          历史记录
        </Link>
        <QrCodeButton />
      </nav>
    </header>
  );
};
