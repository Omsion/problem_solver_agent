import { useEffect, useRef } from "react";
import { useLayoutStore } from "../../stores/useLayoutStore";

/**
 * 全屏看图。
 *
 * 缺陷修正：旧实现取的是 `files[0]`（当前上传队列的第一张），因此在"历史任务"
 * 里点开放大时永远显示错的那张图。现在由调用方传入完整的图片集合与下标。
 */
export const ImageLightbox = () => {
  const images = useLayoutStore((s) => s.lightboxImages);
  const index = useLayoutStore((s) => s.lightboxIndex);
  const close = useLayoutStore((s) => s.closeLightbox);
  const step = useLayoutStore((s) => s.stepLightbox);
  const touchStartX = useRef<number | null>(null);

  const open = images.length > 0;
  const current = open ? images[Math.min(index, images.length - 1)] : "";

  // 键盘操作：Esc 关闭，左右翻页
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
      else if (e.key === "ArrowLeft") step(-1);
      else if (e.key === "ArrowRight") step(1);
    };
    document.addEventListener("keydown", onKey);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prevOverflow;
    };
  }, [open, close, step]);

  if (!open) return null;

  const hasMultiple = images.length > 1;

  return (
    <div
      className="fixed inset-0 z-50 bg-black/95 flex items-center justify-center"
      onClick={close}
      onTouchStart={(e) => {
        touchStartX.current = e.touches[0]?.clientX ?? null;
      }}
      onTouchEnd={(e) => {
        const startX = touchStartX.current;
        touchStartX.current = null;
        if (startX === null || !hasMultiple) return;
        const deltaX = (e.changedTouches[0]?.clientX ?? startX) - startX;
        if (Math.abs(deltaX) > 50) step(deltaX < 0 ? 1 : -1);
      }}
    >
      <button
        onClick={(e) => {
          e.stopPropagation();
          close();
        }}
        className="absolute top-4 right-4 w-10 h-10 rounded-full bg-white/10 hover:bg-white/20 text-white flex items-center justify-center transition-colors z-10 cursor-pointer"
        title="关闭"
      >
        <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
        </svg>
      </button>

      {hasMultiple && (
        <>
          <button
            onClick={(e) => {
              e.stopPropagation();
              step(-1);
            }}
            className="absolute left-2 sm:left-4 w-11 h-11 rounded-full bg-white/10 hover:bg-white/20 text-white flex items-center justify-center transition-colors z-10 cursor-pointer touch-target"
            title="上一张"
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
            </svg>
          </button>
          <button
            onClick={(e) => {
              e.stopPropagation();
              step(1);
            }}
            className="absolute right-2 sm:right-4 w-11 h-11 rounded-full bg-white/10 hover:bg-white/20 text-white flex items-center justify-center transition-colors z-10 cursor-pointer touch-target"
            title="下一张"
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
            </svg>
          </button>
        </>
      )}

      <img
        src={current}
        alt={`题目图片 ${index + 1}`}
        className="max-w-[96vw] max-h-[92vh] object-contain"
        onClick={(e) => e.stopPropagation()}
      />

      {hasMultiple && (
        <p className="absolute bottom-5 left-1/2 -translate-x-1/2 text-white/70 text-sm">
          {index + 1} / {images.length}
        </p>
      )}
    </div>
  );
};
