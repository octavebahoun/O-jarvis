// Conversations et mémoire.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { useEngineStore } from "../store";
import type { Conversation, ConversationDetail, MemoryItem, MemoryKind } from "../types";
import { requireClient } from "./shared";

export function useConversations(query: string) {
  const client = useEngineStore((s) => s.client);
  const q = query.trim();
  return useQuery({
    queryKey: ["conversations", q],
    queryFn: () =>
      requireClient(client).get<Conversation[]>(
        `/v1/conversations?limit=100${q ? `&q=${encodeURIComponent(q)}` : ""}`,
      ),
    enabled: client !== null,
  });
}

export function useConversation(id: string | null) {
  const client = useEngineStore((s) => s.client);
  return useQuery({
    queryKey: ["conversations", "detail", id],
    queryFn: () =>
      requireClient(client).get<ConversationDetail>(
        `/v1/conversations/${encodeURIComponent(id ?? "")}`,
      ),
    enabled: client !== null && id !== null,
  });
}

export function useRenameConversation() {
  const client = useEngineStore((s) => s.client);
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, title }: { id: string; title: string }) =>
      requireClient(client).patch<Conversation>(`/v1/conversations/${encodeURIComponent(id)}`, {
        title,
      }),
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["conversations"] }),
  });
}

export function useDeleteConversation() {
  const client = useEngineStore((s) => s.client);
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      requireClient(client).delete<undefined>(`/v1/conversations/${encodeURIComponent(id)}`),
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["conversations"] }),
  });
}

// ---------------------------------------------------------------- mémoire

export function useMemory(kind: MemoryKind | null, query: string) {
  const client = useEngineStore((s) => s.client);
  const q = query.trim();
  const params = new URLSearchParams();
  if (kind) params.set("kind", kind);
  if (q) params.set("q", q);
  const suffix = params.toString();
  return useQuery({
    queryKey: ["memory", kind, q],
    queryFn: () =>
      requireClient(client).get<MemoryItem[]>(`/v1/memory${suffix ? `?${suffix}` : ""}`),
    enabled: client !== null,
  });
}

export function useAddMemory() {
  const client = useEngineStore((s) => s.client);
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (item: { kind: MemoryKind; content: string }) =>
      requireClient(client).post<MemoryItem>("/v1/memory", item),
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["memory"] }),
  });
}

export function useUpdateMemory() {
  const client = useEngineStore((s) => s.client);
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      id,
      ...changes
    }: {
      id: number;
      kind?: MemoryKind;
      content?: string;
      pinned?: boolean;
      personnel?: boolean;
    }) => requireClient(client).patch<MemoryItem>(`/v1/memory/${id}`, changes),
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["memory"] }),
  });
}

export function useDeleteMemory() {
  const client = useEngineStore((s) => s.client);
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => requireClient(client).delete<undefined>(`/v1/memory/${id}`),
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["memory"] }),
  });
}

// ---------------------------------------------------------------- modèles
