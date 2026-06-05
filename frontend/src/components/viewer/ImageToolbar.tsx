import { useLayoutStore } from "../../stores/useLayoutStore";
import { Button } from "../ui/button";

interface Props {
  zoom: number;
  onZoomIn: () => void;
  onZoomOut: () => void;
  onZoomReset: () => void;
}

export const ImageToolbar = ({ zoom, onZoomIn, onZoomOut, onZoomReset }: Props) => {
  const openLightbox = useLayoutStore((s) => s.openLightbox);

  return (
    <div className="flex items-center gap-1 px-1 py-1.5">
      <Button variant="ghost" size="sm" onClick={onZoomOut} title="缩小">
        <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0zM13 10H7" />
        </svg>
      </Button>
      <span className="text-xs text-gray-500 w-12 text-center tabular-nums">
        {Math.round(zoom * 100)}%
      </span>
      <Button variant="ghost" size="sm" onClick={onZoomIn} title="放大">
        <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0zM10 7v3m0 0v3m0-3h3m-3 0H7" />
        </svg>
      </Button>
      <Button variant="ghost" size="sm" onClick={onZoomReset} title="重置">
        <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
        </svg>
      </Button>
      <div className="flex-1" />
      <Button variant="ghost" size="sm" onClick={() => openLightbox("")} title="全屏">
        <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 8V4m0 0h4M4 4l5 5m11-1V4m0 0h-4m4 0l-5 5M4 16v4m0 0h4m-4 0l5-5m11 5l-5-5m5 5v-4m0 4h-4" />
        </svg>
      </Button>
    </div>
  );
};
