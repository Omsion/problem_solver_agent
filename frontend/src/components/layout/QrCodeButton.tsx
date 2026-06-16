import { useState, useEffect } from "react";
import { useTaskStore } from "../../stores/useTaskStore";
import { useMediaQuery } from "../../hooks/useMediaQuery";

/**
 * "手机扫码"入口。
 *
 * 缺陷 A 的教训：这个按钮曾经因为 `remoteConnected` 被误置为 true 而自己消失。
 * 现在 `remoteConnected` 只用于显示"手机已连接"状态，**不再控制按钮可见性**；
 * 按钮只在两个真正合理的条件下隐藏：
 *   1. 屏幕是移动端尺寸（手机自己不需要扫码入口）
 *   2. 用户主动收起（可选）
 */
interface Props {
  /** 在设置页里强制显示（手机上也需要能看到局域网地址） */
  forceShow?: boolean;
}

export const QrCodeButton = ({ forceShow = false }: Props) => {
  const [open, setOpen] = useState(false);
  const remoteConnected = useTaskStore((s) => s.remoteConnected);

  // 手机/平板自己不需要右上角的扫码入口；但设置页里用 forceShow 显式要求展示
  const isMobileOrTablet = useMediaQuery("(max-width: 1023px)");

  // 关闭弹窗时释放 body 滚动锁
  useEffect(() => {
    if (!open) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.body.style.overflow = prev;
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  if (isMobileOrTablet && !forceShow) return null;

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
        {remoteConnected && (
          <span
            className="w-2 h-2 rounded-full bg-green-500"
            title="手机已连接"
            aria-label="手机已连接"
          />
        )}
      </button>

      {open && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
          onClick={() => setOpen(false)}
        >
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
            {remoteConnected ? (
              <p className="text-xs text-green-600 flex items-center gap-1.5">
                <span className="w-2 h-2 rounded-full bg-green-500" />
                手机已连接
              </p>
            ) : (
              <p className="text-xs text-gray-400">等待手机连接…</p>
            )}
            <button
              onClick={() => setOpen(false)}
              className="px-4 py-2 text-sm font-medium bg-indigo-100 hover:bg-indigo-200 text-indigo-700 rounded-lg transition-colors cursor-pointer"
            >
              关闭
            </button>
          </div>
        </div>
      )}
    </>
  );
};
