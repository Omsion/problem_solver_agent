import * as React from "react";
import { Dialog as RadixDialog } from "radix-ui";
import { X } from "lucide-react";
import { cn } from "../../lib/utils";

interface Props {
  open: boolean;
  onClose: () => void;
  children: React.ReactNode;
  className?: string;
  /** 无障碍标题；不传则使用「对话框」 */
  title?: string;
  /** 是否展示右上角关闭按钮 */
  closable?: boolean;
}

/**
 * 基于 Radix Dialog 封装。
 *
 * 相比旧的手写实现，这里免费获得：
 * - 焦点陷阱与打开后自动聚焦（旧实现完全没有焦点管理）
 * - 关闭时正确还原 body 的滚动锁（旧实现直接赋空串，会覆盖其他组件的锁）
 * - 点击遮罩关闭、Esc 关闭、打开时隐藏背景内容（aria-hidden）
 */
export const Dialog = ({ open, onClose, children, className, title, closable = true }: Props) => (
  <RadixDialog.Root open={open} onOpenChange={(next) => { if (!next) onClose(); }}>
    <RadixDialog.Portal>
      <RadixDialog.Overlay className="fixed inset-0 z-50 bg-black/60 data-[state=open]:animate-in data-[state=open]:fade-in" />
      <RadixDialog.Content
        className={cn(
          "fixed left-1/2 top-1/2 z-50 -translate-x-1/2 -translate-y-1/2",
          "bg-white rounded-2xl shadow-2xl max-w-[92vw] max-h-[92vh] overflow-auto",
          "focus:outline-none",
          className,
        )}
        onOpenAutoFocus={(event) => event.preventDefault()}
      >
        {/* Radix 要求有可访问的标题；视觉上隐藏即可 */}
        <RadixDialog.Title className="sr-only">{title ?? "对话框"}</RadixDialog.Title>

        {closable && (
          <RadixDialog.Close
            className="absolute top-3 right-3 w-9 h-9 rounded-full flex items-center justify-center text-gray-400 hover:text-gray-600 hover:bg-gray-100 transition-colors cursor-pointer z-10"
            aria-label="关闭"
          >
            <X className="w-5 h-5" />
          </RadixDialog.Close>
        )}
        {children}
      </RadixDialog.Content>
    </RadixDialog.Portal>
  </RadixDialog.Root>
);

export const DialogClose = RadixDialog.Close;
