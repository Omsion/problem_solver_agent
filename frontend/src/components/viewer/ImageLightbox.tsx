import { useLayoutStore } from "../../stores/useLayoutStore";
import { useUploadStore } from "../../stores/useUploadStore";
import { Dialog } from "../ui/dialog";

export const ImageLightbox = () => {
  const lightboxUrl = useLayoutStore((s) => s.lightboxUrl);
  const closeLightbox = useLayoutStore((s) => s.closeLightbox);
  const files = useUploadStore((s) => s.files);

  const src = lightboxUrl || files[0]?.previewUrl;
  const filename = files[0]?.file.name ?? "";

  if (!src && !lightboxUrl) return null;

  return (
    <Dialog open={!!lightboxUrl} onClose={closeLightbox} className="bg-transparent p-0 max-w-none max-h-none w-screen h-screen">
      <div className="relative w-screen h-screen flex items-center justify-center bg-black/95">
        <button
          onClick={closeLightbox}
          className="absolute top-4 right-4 w-10 h-10 rounded-full bg-white/10 hover:bg-white/20 text-white flex items-center justify-center transition-colors z-10 cursor-pointer"
        >
          <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
        <img
          src={lightboxUrl || src || ""}
          alt={filename}
          className="max-w-[95vw] max-h-[95vh] object-contain"
        />
        {filename && (
          <p className="absolute bottom-4 left-1/2 -translate-x-1/2 text-white/60 text-sm">
            {filename}
          </p>
        )}
      </div>
    </Dialog>
  );
};
