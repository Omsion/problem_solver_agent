import { useEffect, useMemo, useRef, useState } from "react";
import { useUploadStore } from "../../stores/useUploadStore";
import { useLayoutStore } from "../../stores/useLayoutStore";
import { useIsMobile } from "../../hooks/useMediaQuery";

const MIN_SCALE = 1;
const MAX_SCALE = 4;

/**
 * 已选题目的图片预览。
 *
 * 移动端支持双指捏合缩放（旧实现只绑了 onDoubleClick，触屏上永不触发，
 * 却在界面上提示"双击放大查看"）。桌面端保留双击打开全屏。
 */
export const ImageViewer = () => {
  const files = useUploadStore((s) => s.files);
  const openLightbox = useLayoutStore((s) => s.openLightbox);
  const isMobile = useIsMobile();
  const [selectedIdx, setSelectedIdx] = useState(0);
  const [scale, setScale] = useState(MIN_SCALE);
  const pinchStart = useRef<{ distance: number; scale: number } | null>(null);

  const urls = useMemo(() => files.map((f) => f.previewUrl), [files]);

  // 切换图片时复位缩放
  useEffect(() => {
    setScale(MIN_SCALE);
  }, [selectedIdx]);

  if (files.length === 0) return null;

  const safeIdx = Math.min(selectedIdx, files.length - 1);
  const current = files[safeIdx];

  const distanceBetween = (touches: ArrayLike<{ clientX: number; clientY: number }>): number => {
    if (touches.length < 2) return 0;
    const a = touches[0];
    const b = touches[1];
    return Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);
  };

  return (
    <div className="flex flex-col">
      <div className="relative flex-1 flex items-center justify-center bg-gray-100 rounded-lg overflow-hidden min-h-[200px]">
        <img
          src={current.previewUrl}
          alt={current.file.name}
          style={scale > 1 ? { transform: `scale(${scale})` } : undefined}
          className="max-w-full max-h-full object-contain transition-transform duration-100"
          onDoubleClick={() => openLightbox(urls, safeIdx)}
          onTouchStart={(e) => {
            if (e.touches.length === 2) {
              pinchStart.current = { distance: distanceBetween(e.touches), scale };
            }
          }}
          onTouchMove={(e) => {
            if (e.touches.length !== 2 || !pinchStart.current) return;
            const ratio = distanceBetween(e.touches) / (pinchStart.current.distance || 1);
            const next = Math.min(Math.max(pinchStart.current.scale * ratio, MIN_SCALE), MAX_SCALE);
            setScale(next);
          }}
          onTouchEnd={() => {
            pinchStart.current = null;
            if (scale <= MIN_SCALE + 0.05) setScale(MIN_SCALE);
          }}
        />

        <button
          onClick={() => openLightbox(urls, safeIdx)}
          className="absolute top-2 right-2 px-2.5 py-1.5 rounded-md bg-black/50 text-white text-xs hover:bg-black/70 transition-colors cursor-pointer touch-target"
          title="全屏查看"
        >
          全屏
        </button>

        {scale > MIN_SCALE && (
          <button
            onClick={() => setScale(MIN_SCALE)}
            className="absolute bottom-2 right-2 px-2.5 py-1.5 rounded-md bg-black/50 text-white text-xs hover:bg-black/70 transition-colors cursor-pointer touch-target"
            title="恢复原始大小"
          >
            {Math.round(scale * 100)}%
          </button>
        )}
      </div>

      <p className="text-xs text-gray-400 mt-1.5 text-center no-select">
        {isMobile ? "双指缩放 · 点「全屏」查看大图" : "双击图片或点「全屏」查看大图"}
      </p>

      {files.length > 1 && (
        <div className="flex gap-2 mt-2 pb-1 overflow-x-auto">
          {files.map((f, i) => (
            <button
              key={f.id}
              onClick={() => setSelectedIdx(i)}
              className={`flex-shrink-0 rounded-lg overflow-hidden border-2 transition-colors cursor-pointer ${
                isMobile ? "w-16 h-16" : "w-14 h-14"
              } ${i === safeIdx ? "border-indigo-500 ring-2 ring-indigo-200" : "border-gray-200 hover:border-gray-400"}`}
              title={f.file.name}
            >
              <img src={f.previewUrl} alt={f.file.name} className="w-full h-full object-cover" />
            </button>
          ))}
        </div>
      )}
    </div>
  );
};
