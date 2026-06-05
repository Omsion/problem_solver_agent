import { useUploadStore } from "../../stores/useUploadStore";
import { Button } from "../ui/button";

interface Props {
  onStart: () => void;
  loading?: boolean;
}

export const UploadActions = ({ onStart, loading }: Props) => {
  const files = useUploadStore((s) => s.files);
  const clearFiles = useUploadStore((s) => s.clearFiles);

  if (files.length === 0) return null;

  return (
    <div className="flex items-center justify-between gap-3 pt-3">
      <Button variant="ghost" size="sm" onClick={clearFiles}>
        清除全部
      </Button>
      <Button size="lg" onClick={onStart} disabled={loading}>
        {loading ? (
          <>
            <svg className="animate-spin w-4 h-4" viewBox="0 0 24 24">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
            </svg>
            处理中...
          </>
        ) : (
          "开始解答"
        )}
      </Button>
    </div>
  );
};
