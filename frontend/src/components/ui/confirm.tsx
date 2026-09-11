import * as React from "react";
import { AlertDialog as RadixAlertDialog } from "radix-ui";
import { cn } from "../../lib/utils";

export interface ConfirmOptions {
  title: string;
  description?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  /** 危险操作（删除等）用红色确认按钮 */
  destructive?: boolean;
}

type ConfirmFn = (options: ConfirmOptions) => Promise<boolean>;

const ConfirmContext = React.createContext<ConfirmFn | null>(null);

/**
 * 用 Promise 风格的确认框替代 `window.confirm`。
 *
 * 原生 confirm 的问题：样式无法控制、移动端体验差、阻塞主线程、
 * 与页面视觉语言完全脱节。这里用 Radix AlertDialog 实现，API 保持一致：
 *
 *     const confirm = useConfirm();
 *     if (!(await confirm({ title: "确认删除？", destructive: true }))) return;
 */
export const ConfirmProvider = ({ children }: { children: React.ReactNode }) => {
  const [state, setState] = React.useState<{
    options: ConfirmOptions;
    resolve: (value: boolean) => void;
  } | null>(null);

  const confirm = React.useCallback<ConfirmFn>(
    (options) =>
      new Promise<boolean>((resolve) => {
        setState({ options, resolve });
      }),
    [],
  );

  const settle = (value: boolean) => {
    state?.resolve(value);
    setState(null);
  };

  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      <RadixAlertDialog.Root
        open={state !== null}
        onOpenChange={(open) => {
          // Esc / 点击遮罩关闭 = 取消
          if (!open) settle(false);
        }}
      >
        <RadixAlertDialog.Portal>
          <RadixAlertDialog.Overlay className="fixed inset-0 z-[60] bg-black/60" />
          <RadixAlertDialog.Content className="fixed left-1/2 top-1/2 z-[60] w-[92vw] max-w-sm -translate-x-1/2 -translate-y-1/2 rounded-2xl bg-white p-5 shadow-2xl focus:outline-none">
            <RadixAlertDialog.Title className="text-base font-semibold text-gray-900">
              {state?.options.title}
            </RadixAlertDialog.Title>
            {state?.options.description && (
              <RadixAlertDialog.Description className="mt-2 text-sm text-gray-500">
                {state.options.description}
              </RadixAlertDialog.Description>
            )}
            <div className="mt-5 flex justify-end gap-2">
              <RadixAlertDialog.Cancel
                onClick={() => settle(false)}
                className="rounded-lg bg-gray-100 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-200 transition-colors cursor-pointer"
              >
                {state?.options.cancelLabel ?? "取消"}
              </RadixAlertDialog.Cancel>
              <button
                onClick={() => settle(true)}
                className={cn(
                  "rounded-lg px-4 py-2 text-sm font-medium text-white transition-colors cursor-pointer",
                  state?.options.destructive ? "bg-red-600 hover:bg-red-700" : "bg-indigo-600 hover:bg-indigo-700",
                )}
              >
                {state?.options.confirmLabel ?? "确认"}
              </button>
            </div>
          </RadixAlertDialog.Content>
        </RadixAlertDialog.Portal>
      </RadixAlertDialog.Root>
    </ConfirmContext.Provider>
  );
};

export function useConfirm(): ConfirmFn {
  const context = React.useContext(ConfirmContext);
  if (!context) throw new Error("useConfirm 必须在 ConfirmProvider 内使用");
  return context;
}
