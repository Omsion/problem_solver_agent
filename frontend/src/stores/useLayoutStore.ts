import { create } from "zustand";

interface LayoutState {
  leftPanelRatio: number;
  setLeftPanelRatio: (r: number) => void;
  lightboxUrl: string | null;
  openLightbox: (url: string) => void;
  closeLightbox: () => void;
}

export const useLayoutStore = create<LayoutState>((set) => ({
  leftPanelRatio: 45,
  setLeftPanelRatio: (r) => set({ leftPanelRatio: r }),

  lightboxUrl: null,
  openLightbox: (url) => set({ lightboxUrl: url }),
  closeLightbox: () => set({ lightboxUrl: null }),
}));
