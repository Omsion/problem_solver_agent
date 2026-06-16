import { useState, useEffect } from "react";
import { useTaskStore } from "../../stores/useTaskStore";
import { useMediaQuery } from "../../hooks/useMediaQuery";

export const QrCodeButton = () => {
  const [open, setOpen] = useState(false);
  const remoteConnected = useTaskStore((s: any) => s.remoteConnected);
  const setRemoteConnected = useTaskStore((s: any) => s.setRemoteConnected);

  // 移动端判断：屏幕宽度小于 1024px 时认为是移动设备，不显示扫码按钮
  const isMobileOrTablet = useMediaQuery("(max-width: 1023px)");

  // 调试用：在 localStorage 中可以强制显示/隐藏
  const [forceShow, setForceShow] = useState<boolean | null>(null);
  useEffect(() => {
    const forced = localStorage.getItem("forceShowQr");
    if (forced === "1") setForceShow(true);
    else if (forced === "0") setForceShow(false);
  }, []);

  // 如果是移动端或远程设备已连接，则不显示按钮
  const shouldShow = !isMobileOrTablet && !remoteConnected;
  if (forceShow === false) return null;
  if (!shouldShow && forceShow !== true) return null;

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
            <div className="flex gap-2">
              <button
                onClick={() => {
                  setRemoteConnected(true);
                  setOpen(false);
                }}
                className="px-4 py-2 text-sm font-medium text-gray-600 bg-gray-100 hover:bg-gray-200 rounded-lg transition-colors cursor-pointer"
              >
                模拟连接
              </button>
              <button
                onClick={() => setOpen(false)}
                className="px-4 py-2 text-sm font-medium bg-indigo-100 hover:bg-indigo-200 text-indigo-700 rounded-lg transition-colors cursor-pointer"
              >
                关闭
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
};
