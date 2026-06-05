import { create } from "zustand";
import type { UploadFile } from "../types";
import { uid } from "../lib/utils";

interface UploadState {
  files: UploadFile[];
  addFiles: (fileList: File[]) => void;
  removeFile: (id: string) => void;
  clearFiles: () => void;
}

export const useUploadStore = create<UploadState>((set) => ({
  files: [],

  addFiles: (fileList) => {
    const newFiles: UploadFile[] = fileList.map((file) => ({
      id: uid(),
      file,
      previewUrl: URL.createObjectURL(file),
    }));
    set((s) => ({ files: [...s.files, ...newFiles] }));
  },

  removeFile: (id) => {
    set((s) => {
      const file = s.files.find((f) => f.id === id);
      if (file) URL.revokeObjectURL(file.previewUrl);
      return { files: s.files.filter((f) => f.id !== id) };
    });
  },

  clearFiles: () => {
    set((s) => {
      for (const f of s.files) URL.revokeObjectURL(f.previewUrl);
      return { files: [] };
    });
  },
}));
