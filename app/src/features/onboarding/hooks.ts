import { useMutation, useQuery } from "@tanstack/react-query";

import { EngineApiError } from "../../lib/engine/client";
import { requireClient } from "../../lib/engine/hooks/shared";
import { useEngineStore } from "../../lib/engine/store";

/** L'extension Chrome est-elle branchée ? Vient d'O-jarvis Shell : `null` s'il est absent. */
export function useChromeStatus(enabled: boolean) {
  const client = useEngineStore((s) => s.client);
  return useQuery({
    queryKey: ["chrome", "status"],
    queryFn: async () => {
      try {
        return await requireClient(client).get<{ connected: boolean }>("/v1/chrome/status");
      } catch (error) {
        if (error instanceof EngineApiError && error.status === 404) return null;
        throw error;
      }
    },
    enabled: enabled && client !== null,
    refetchInterval: 3000,
  });
}

/** Demander tout de suite à GNOME l'accord de contrôler le pointeur et le clavier. */
export function useAuthorizePointer() {
  const client = useEngineStore((s) => s.client);
  return useMutation({
    mutationFn: () =>
      requireClient(client).post<{ authorized: boolean }>("/v1/desktop/pointer/authorize"),
  });
}
