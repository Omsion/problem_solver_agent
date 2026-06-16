import { create } from "zustand";

interface LayoutState {
  leftPanelRatio: number;
  setLeftPanelRatio: (r: number) => void;

  /** 灯箱当前展示的图片集合与下标（支持左右翻页与滑动切图） */
  lightboxImages: string[];
  lightboxIndex: number;
  openLightbox: (images: string[], index?: number) => void;
  closeLightbox: () => void;
  stepLightbox: (delta: number) => void;
}

export const useLayoutStore = create<LayoutState>((set) => ({
  leftPanelRatio: 45,
  setLeftPanelRatio: (r) => set({ leftPanelRatio: r }),

  lightboxImages: [],
  lightboxIndex: 0,
  openLightbox: (images, index = 0) =>
    set({
      lightboxImages: images,
      lightboxIndex: Math.max(0, Math.min(index, Math.max(images.length - 1, 0))),
    }),
  closeLightbox: () => set({ lightboxImages: [] }),
  stepLightbox: (delta) =>
    set((s) => {
      if (s.lightboxImages.length === 0) return {};
      const next = (s.lightboxIndex + delta + s.lightboxImages.length) % s.lightboxImages.length;
      return { lightboxIndex: next };
    }),
}));
