import { Toaster, toast } from "sonner";

/**
 * 轻提示统一入口。
 *
 * 替代原来的 `alert()` 与页面内联横幅：
 * - 不阻塞主线程、可堆叠、自动消失
 * - 手机上出现在顶部/底部安全区域，不会被键盘遮挡太久
 * - 视觉与页面一致（Tailwind 变量主题）
 */
export const Toasts = () => (
  <Toaster
    position="top-center"
    richColors
    closeButton
    duration={3200}
    toastOptions={{
      classNames: {
        toast: "rounded-xl text-sm",
        description: "text-xs opacity-90",
      },
    }}
  />
);

export const notify = {
  success: (message: string, description?: string) => toast.success(message, { description }),
  error: (message: string, description?: string) => toast.error(message, { description }),
  info: (message: string, description?: string) => toast(message, { description }),
  /** 新任务到达等需要用户注意但不报错的通知 */
  attention: (message: string, description?: string) => toast.info(message, { description }),
};

export { toast };
