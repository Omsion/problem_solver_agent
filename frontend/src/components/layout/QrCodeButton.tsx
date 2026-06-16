import { useState } from "react";
import { useTaskStore } from "../../stores/useTaskStore";
import { useIsMobile, useMediaQuery } from "../../hooks/useMediaQuery";

export const QrCodeButton = () => {
  const [open, setOpen] = useState(false);
  const remoteConnected = useTaskStore((s) => s.remoteConnected);
  const isMobile = useIsMobile();

  // 移动端不显示扫码按钮（用户已在手机上访问）
  const isMobileOrSmall = useMediaQuery("(max-width: 1023px)");
  if (isMobile || isMobileOrSmall) return null;

  // 远程客户端已连接 → 隐藏按钮
  if (remoteConnected) return null;

  return (
    <>
      <button
        onClick={() => setOpen(true)}
        className="px-3 py-1.5 text-sm font-medium rounded-md text-indigo-600 bg-indigo-50 hover:bg-indigo-100 border border-indigo-200 cursor-pointer transition-colors flex items-center gap-1.5"
        title="手机扫码访问"
      >
        <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
            d="M12 4v1m6 11h2m-6 0h-2m0 0H8m4 0h4m0 0v-1m0-5V7a1 1 0 00-1-1h-4a1 1 0 00-1 1v3m4 0H9m3 0v3m0-3V7m0 11v-4" />
        </svg>
        <span className="hidden sm:inline">手机扫码</span>
      </button>

      {open && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={() => setOpen(false)}>
          <div
            className="bg-white rounded-xl shadow-xl p-6 flex flex-col items-center gap-4 max-w-xs w-full mx-4"
            onClick={(e) => e.stopPropagation()}
          >
            <h3 className="text-base font-semibold text-gray-900">手机扫码访问</h3>
            <img
              src="/api/qrcode"
              alt="局域网访问二维码"
              className="w-56 h-56 rounded-lg border border-gray-200"
            />
            <p className="text-xs text-gray-400 text-center">
              手机与电脑连接同一局域网后，扫码即可在手机上使用
            </p>
            <button
              onClick={() => setOpen(false)}
              className="px-4 py-2 text-sm font-medium bg-gray-100 hover:bg-gray-200 rounded-lg transition-colors cursor-pointer"
            >
              关闭
            </button>
          </div>
        </div>
      )}
    </>
  );
};
