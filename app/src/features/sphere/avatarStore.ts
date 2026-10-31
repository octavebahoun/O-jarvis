import { create } from "zustand";

import type { AvatarModel } from "./renderer";

const STORAGE_KEY = "ojarvis.avatar_model";

function getInitialModel(): AvatarModel {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved === "sphere" || saved === "cyberMask") {
      return saved;
    }
  } catch {
    // Si localStorage n'est pas accessible
  }
  return "sphere";
}

interface AvatarStore {
  avatarModel: AvatarModel;
  setAvatarModel: (model: AvatarModel) => void;
}

export const useAvatarStore = create<AvatarStore>((set) => ({
  avatarModel: getInitialModel(),
  setAvatarModel: (model) => {
    try {
      localStorage.setItem(STORAGE_KEY, model);
    } catch {
      // Ignorer si localStorage échoue
    }
    set({ avatarModel: model });
  },
}));
