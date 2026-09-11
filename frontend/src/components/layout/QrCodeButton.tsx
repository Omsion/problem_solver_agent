import { useState } from "react";
import { QrCode } from "lucide-react";
import { Dialog } from "../ui/dialog";
import { useTaskStore } from "../../stores/useTaskStore";
import { useMediaQuery } from "../../hooks/useMediaQuery";

interface Props {
  /** 在设置页里强制显示（手机上也需要能看到局域网地址） */
  forceShow?: boolean;
}

/**
 * "手机扫码"入口。
 *
 * 缺陷 A 的教训：这个按钮曾因 `remoteConnected` 被误置为 true 而自己消失。
 * 现在 `remoteConnected` 只用于显示"手机已连接"状态，**不控制按钮可见性**；
 * 仅在移动端尺寸下隐藏（手机自己不需要扫码），设置页可用 forceShow 覆盖。
 */
export const QrCodeButton = ({ forceShow = false }: Props) => {
  const [open, setOpen] = useState(false);
  const remoteConnected = useTaskStore((s) => s.remoteConnected);
  const isMobileOrTablet = useMediaQuery("(max-width: 1023px)");

  if (isMobileOrTablet && !forceShow) return null;

  return (
    <>
      <button
        onClick={() => setOpen(true)}
        className="px-3 py-1.5 text-sm font-medium rounded-md text-indigo-600 bg-indigo-50 hover:bg-indigo-100 border border-indigo-200 cursor-pointer transition-colors flex items-center gap-1.5"
        title="手机扫码访问"
      >
        <QrCode className="w-4 h-4" />
        <span className="hidden sm:inline">手机扫码</span>
        {remoteConnected && (
          <span className="w-2 h-2 rounded-full bg-green-500" title="手机已连接" aria-label="手机已连接" />
        )}
      </button>

      <Dialog open={open} onClose={() => setOpen(false)} title="手机扫码访问" className="max-w-xs w-full mx-4">
        <div className="p-6 flex flex-col items-center gap-4">
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
      </Dialog>
    </>
  );
};
