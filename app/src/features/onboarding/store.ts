import { create } from "zustand";

import { runtime } from "../../lib/engine/connection";

/** Retenu sur ce PC : l'accueil ne revient plus une fois terminé ou passé. */
const SEEN = "ojarvis.accueil.vu";

function seen(): boolean {
  try {
    return localStorage.getItem(SEEN) === "1";
  } catch {
    return false;
  }
}

/** L'accueil du premier lancement (dans l'app installée) ; Réglages › Démarrage le rouvre. */
export const useOnboarding = create<{ open: boolean; show: () => void; finish: () => void }>(
  (set) => ({
    open: runtime === "tauri" && !seen(),
    show: () => {
      set({ open: true });
    },
    finish: () => {
      try {
        localStorage.setItem(SEEN, "1");
      } catch {
        // stockage refusé : l'accueil reviendra au prochain lancement, rien de grave
      }
      set({ open: false });
    },
  }),
);
