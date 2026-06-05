import { useCallback, useRef, useState, type DragEvent, type ClipboardEvent } from "react";
import { useUploadStore } from "../../stores/useUploadStore";

export const UploadZone = () => {
  const addFiles = useUploadStore((s) => s.addFiles);
  const [dragOver, setDragOver] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const handleFiles = useCallback(
    (fileList: FileList | null) => {
      if (!fileList) return;
      const images = Array.from(fileList).filter((f) => f.type.startsWith("image/"));
      if (images.length) addFiles(images);
    },
    [addFiles],
  );

  const onDragOver = (e: DragEvent) => {
    e.preventDefault();
    setDragOver(true);
  };
  const onDragLeave = () => setDragOver(false);
  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    handleFiles(e.dataTransfer.files);
  };

  const onPaste = useCallback(
    (e: ClipboardEvent) => {
      const items = e.clipboardData?.items;
      if (!items) return;
      const files: File[] = [];
      for (let i = 0; i < items.length; i++) {
        if (items[i].type.startsWith("image/")) {
          const file = items[i].getAsFile();
          if (file) files.push(file);
        }
      }
      if (files.length) addFiles(files);
    },
    [addFiles],
  );

  return (
    <div
      className={`relative flex flex-col items-center justify-center gap-3 p-10 border-2 border-dashed rounded-xl transition-colors cursor-pointer min-h-[200px] ${
        dragOver
          ? "border-indigo-400 bg-indigo-50"
          : "border-gray-300 bg-gray-50 hover:border-gray-400 hover:bg-gray-100"
      }`}
      onDragOver={onDragOver}
      onDragLeave={onDragLeave}
      onDrop={onDrop}
      onPaste={onPaste}
      onClick={() => inputRef.current?.click()}
      tabIndex={0}
      role="button"
    >
      <svg className="w-10 h-10 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
          d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z" />
      </svg>
      <p className="text-sm text-gray-500">拖拽图片到此处，或点击上传 / 粘贴</p>
      <p className="text-xs text-gray-400">支持 PNG、JPG、WEBP 格式</p>
      <input
        ref={inputRef}
        type="file"
        accept="image/*"
        multiple
        className="hidden"
        onChange={(e) => handleFiles(e.target.files)}
      />
    </div>
  );
};
