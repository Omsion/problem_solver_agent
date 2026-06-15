import { useState } from "react";
import { useUploadStore } from "../../stores/useUploadStore";
import { useLayoutStore } from "../../stores/useLayoutStore";
import { useIsMobile } from "../../hooks/useMediaQuery";

export const ImageViewer = () => {
  const files = useUploadStore((s) => s.files);
  const openLightbox = useLayoutStore((s) => s.openLightbox);
  const isMobile = useIsMobile();
  const [selectedIdx, setSelectedIdx] = useState(0);

  if (files.length === 0) return null;

  const current = files[selectedIdx] ?? files[0];

  return (
    <div className="flex flex-col h-full">
      {/* Main image area */}
      <div className="flex-1 flex flex-col items-center justify-center bg-gray-100 rounded-lg overflow-hidden min-h-0">
        <img
          src={current.previewUrl}
          alt={current.file.name}
          className="max-w-full max-h-full object-contain cursor-zoom-in"
          onDoubleClick={() => openLightbox(current.previewUrl)}
        />
        {isMobile && (
          <p className="text-xs text-gray-400 mt-2 text-center no-select">
            双击放大查看
          </p>
        )}
      </div>

      {/* Thumbnail strip */}
      {files.length > 1 && (
        <div className="flex gap-2 mt-3 pb-1 overflow-x-auto">
          {files.map((f, i) => (
            <button
              key={f.id}
              onClick={() => setSelectedIdx(i)}
              className={`flex-shrink-0 rounded-lg overflow-hidden border-2 transition-colors cursor-pointer ${
                isMobile ? "w-16 h-16" : "w-14 h-14"
              } ${
                i === selectedIdx ? "border-indigo-500 ring-2 ring-indigo-200" : "border-gray-200 hover:border-gray-400"
              }`}
            >
              <img src={f.previewUrl} alt={f.file.name} className="w-full h-full object-cover" />
            </button>
          ))}
        </div>
      )}
    </div>
  );
};
