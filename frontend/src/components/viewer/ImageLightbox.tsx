import { useEffect, useRef } from "react";
import { ChevronLeft, ChevronRight, X } from "lucide-react";
import { Dialog } from "../ui/dialog";
import { useLayoutStore } from "../../stores/useLayoutStore";

/**
 * 全屏看图。
 *
 * 基于 Radix Dialog：获得焦点陷阱、Esc 关闭、滚动锁正确还原。
 * 支持左右翻页（按钮 / 方向键 / 手机左右滑动）。
 */
export const ImageLightbox = () => {
  const images = useLayoutStore((s) => s.lightboxImages);
  const index = useLayoutStore((s) => s.lightboxIndex);
  const close = useLayoutStore((s) => s.closeLightbox);
  const step = useLayoutStore((s) => s.stepLightbox);
  const touchStartX = useRef<number | null>(null);

  const open = images.length > 0;
  const safeIndex = Math.min(index, Math.max(images.length - 1, 0));
  const current = open ? images[safeIndex] : "";

  // 左右方向键翻页（Esc 由 Dialog 处理）
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "ArrowLeft") step(-1);
      else if (event.key === "ArrowRight") step(1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, step]);

  const hasMultiple = images.length > 1;

  return (
    <Dialog
      open={open}
      onClose={close}
      title="查看题目图片"
      closable={false}
      className="bg-transparent p-0 max-w-none max-h-none w-screen h-screen shadow-none rounded-none overflow-hidden"
    >
      <div
        className="relative w-screen h-screen flex items-center justify-center bg-black/95"
        onTouchStart={(event) => {
          touchStartX.current = event.touches[0]?.clientX ?? null;
        }}
        onTouchEnd={(event) => {
          const startX = touchStartX.current;
          touchStartX.current = null;
          if (startX === null || !hasMultiple) return;
          const deltaX = (event.changedTouches[0]?.clientX ?? startX) - startX;
          if (Math.abs(deltaX) > 50) step(deltaX < 0 ? 1 : -1);
        }}
      >
        <button
          onClick={close}
          className="absolute top-4 right-4 w-10 h-10 rounded-full bg-white/10 hover:bg-white/20 text-white flex items-center justify-center transition-colors z-10 cursor-pointer"
          title="关闭"
          aria-label="关闭"
        >
          <X className="w-5 h-5" />
        </button>

        {hasMultiple && (
          <>
            <button
              onClick={() => step(-1)}
              className="absolute left-2 sm:left-4 w-11 h-11 rounded-full bg-white/10 hover:bg-white/20 text-white flex items-center justify-center transition-colors z-10 cursor-pointer touch-target"
              title="上一张"
              aria-label="上一张"
            >
              <ChevronLeft className="w-5 h-5" />
            </button>
            <button
              onClick={() => step(1)}
              className="absolute right-2 sm:right-4 w-11 h-11 rounded-full bg-white/10 hover:bg-white/20 text-white flex items-center justify-center transition-colors z-10 cursor-pointer touch-target"
              title="下一张"
              aria-label="下一张"
            >
              <ChevronRight className="w-5 h-5" />
            </button>
          </>
        )}

        <img
          src={current}
          alt={`题目图片 ${safeIndex + 1}`}
          className="max-w-[96vw] max-h-[92vh] object-contain"
        />

        {hasMultiple && (
          <p className="absolute bottom-5 left-1/2 -translate-x-1/2 text-white/70 text-sm tabular-nums">
            {safeIndex + 1} / {images.length}
          </p>
        )}
      </div>
    </Dialog>
  );
};
