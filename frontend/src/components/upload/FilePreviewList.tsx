import { useUploadStore } from "../../stores/useUploadStore";
import { Card, CardContent } from "../ui/card";

export const FilePreviewList = () => {
  const files = useUploadStore((s) => s.files);
  const removeFile = useUploadStore((s) => s.removeFile);

  if (files.length === 0) {
    return (
      <Card variant="bordered" className="p-8 text-center">
        <p className="text-sm text-gray-400">暂无图片</p>
      </Card>
    );
  }

  return (
    <Card>
      <CardContent className="p-3">
        <div className="flex items-center justify-between mb-2">
          <span className="text-xs font-medium text-gray-500">
            已选择 {files.length} 张图片
          </span>
        </div>
        <div className="grid grid-cols-4 gap-2">
          {files.map((f) => (
            <div key={f.id} className="relative group rounded-lg overflow-hidden aspect-square bg-gray-100">
              <img
                src={f.previewUrl}
                alt={f.file.name}
                className="w-full h-full object-cover"
              />
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  removeFile(f.id);
                }}
                className="absolute top-1 right-1 w-5 h-5 rounded-full bg-red-500 text-white text-xs flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity cursor-pointer"
                title="移除"
              >
                &times;
              </button>
              <p className="absolute bottom-0 inset-x-0 bg-black/50 text-white text-[10px] px-1 py-0.5 truncate">
                {f.file.name}
              </p>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
};
