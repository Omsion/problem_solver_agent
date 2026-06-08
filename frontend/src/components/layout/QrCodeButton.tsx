import { useEffect, useState } from "react";

export const QrCodeButton = () => {
  const [open, setOpen] = useState(false);
  const [isTouch, setIsTouch] = useState(false);

  useEffect(() => {
    setIsTouch("ontouchstart" in window || navigator.maxTouchPoints > 0);
  }, []);

  // 在触摸设备上隐藏扫码按钮（用户已在手机上访问）
  if (isTouch) return null;

  return (
    <>
      <button
        onClick={() => setOpen(true)}
        className="px-3 py-1.5 text-sm font-medium rounded-md text-gray-500 hover:text-gray-700 hover:bg-gray-100 cursor-pointer transition-colors"
        title="手机扫码访问"
      >
        📱 手机扫码
      </button>

      {open && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={() => setOpen(false)}>
          <div
            className="bg-white rounded-xl shadow-xl p-6 flex flex-col items-center gap-4 max-w-xs w-full"
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
