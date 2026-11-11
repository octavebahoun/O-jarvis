import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useMemo, useRef, useState } from "react";

import { describeError } from "../../lib/engine/client";
import { useConversation } from "../../lib/engine/queries";
import { readEvents } from "../../lib/engine/sse";
import { useEngineClient } from "../../lib/engine/store";
import type {
  ContextItemSummary,
  StoredMessage,
  StrongerModel,
  ToolActivity,
} from "../../lib/engine/types";
import type { SphereState } from "../sphere/states";

export interface TurnMeta {
  provider: string | null;
  model: string | null;
  latencyMs: number | null;
  firstTokenMs: number | null;
  costUsd: number | null;
  traceId: string | null;
  fallbackFrom: string | null;
}

export interface ChatItem {
  key: string;
  id: number | null;
  role: "user" | "assistant";
  content: string;
  status: StoredMessage["status"];
  meta: TurnMeta | null;
  context: ContextItemSummary[] | null;
  error: string | null;
  /** Outils utilisés pendant le tour (lecture d'une page, recherche…). */
  tools: ToolActivity[];
  /** Plans proposés pendant le tour : ils attendent ta décision. */
  plans: string[];
  /** Une tâche lourde : refaire avec ce modèle plus fort, si tu le veux. */
  stronger: StrongerModel | null;
}

export type ChatPhase = "idle" | "waiting" | "streaming";

function fromStored(message: StoredMessage): ChatItem {
  return {
    key: `m${message.id}`,
    id: message.id,
    role: message.role,
    content: message.content,
    status: message.status,
    meta:
      message.role === "assistant"
        ? {
            provider: message.provider,
            model: message.model,
            latencyMs: message.latency_ms,
            firstTokenMs: null,
            costUsd: message.cost_usd,
            traceId: message.trace_id,
            fallbackFrom: null,
          }
        : null,
    context: null,
    error: message.status === "error" ? "La réponse n'a pas pu aboutir." : null,
    tools: message.meta?.tools ?? [],
    plans: message.meta?.plans ?? [],
    stronger: null,
  };
}

/**
 * Messages enregistrés + tour en direct. Une fois la réponse enregistrée, la conversation se
 * recharge : le message enregistré garde ce que seul le direct connaît, les souvenirs utilisés
 * et la proposition d'un modèle plus fort.
 */
export function withLive(stored: ChatItem[], live: ChatItem[]): ChatItem[] {
  const direct = new Map(live.filter((m) => m.id !== null).map((m) => [m.id, m]));
  const merged = stored.map((m) => {
    const turn = direct.get(m.id);
    return turn
      ? { ...m, context: turn.context ?? m.context, stronger: turn.stronger ?? m.stronger }
      : m;
  });
  const known = new Set(stored.map((m) => m.id));
  return [...merged, ...live.filter((m) => m.id === null || !known.has(m.id))];
}

/** Ajoute ou met à jour l'activité d'un outil (même appel : même identifiant). */
function upsertTool(tools: ToolActivity[], tool: ToolActivity): ToolActivity[] {
  const index = tools.findIndex((t) => t.id === tool.id);
  if (index === -1) return [...tools, tool];
  return tools.map((t, i) => (i === index ? { ...t, ...tool } : t));
}

/**
 * Conversation en cours : messages enregistrés + tour en direct (flux SSE).
 * Le niveau « audio » de la sphère suit le rythme d'arrivée du texte.
 */
export function useChat(conversationId: string | null, onConversation: (id: string) => void) {
  const client = useEngineClient();
  const queryClient = useQueryClient();
  const detail = useConversation(conversationId);
  const [live, setLive] = useState<ChatItem[]>([]);
  // Conversation à laquelle appartient le tour en direct (null : pas encore créée).
  const [liveFor, setLiveFor] = useState<string | null>(conversationId);
  const [phase, setPhase] = useState<ChatPhase>("idle");
  const [failed, setFailed] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const levelRef = useRef(0);

  const stored = useMemo(() => (detail.data?.messages ?? []).map(fromStored), [detail.data]);
  const items = useMemo(() => {
    const visibleLive = liveFor === conversationId || phase !== "idle" ? live : [];
    return withLive(stored, visibleLive);
  }, [stored, live, liveFor, conversationId, phase]);

  const patchLive = useCallback((key: string, change: Partial<ChatItem>) => {
    setLive((current) => current.map((m) => (m.key === key ? { ...m, ...change } : m)));
  }, []);

  const send = useCallback(
    async (text: string, model?: string) => {
      const trimmed = text.trim();
      if (!client || !trimmed || phase !== "idle") return;
      const stamp = Date.now();
      const userKey = `u${stamp}`;
      const replyKey = `a${stamp}`;
      setFailed(false);
      setLiveFor(conversationId);
      setLive((current) => [
        ...(liveFor === conversationId ? current : []),
        {
          key: userKey,
          id: null,
          role: "user",
          content: trimmed,
          status: "ok",
          meta: null,
          context: null,
          error: null,
          tools: [],
          plans: [],
          stronger: null,
        },
        {
          key: replyKey,
          id: null,
          role: "assistant",
          content: "",
          status: "streaming",
          meta: null,
          context: null,
          error: null,
          tools: [],
          plans: [],
          stronger: null,
        },
      ]);
      setPhase("waiting");
      const controller = new AbortController();
      abortRef.current = controller;
      let content = "";
      let fallbackFrom: string | null = null;
      let tools: ToolActivity[] = [];
      const plans: string[] = [];
      try {
        const response = await client.stream(
          "/v1/chat",
          { text: trimmed, conversation_id: conversationId, ...(model ? { model } : {}) },
          controller.signal,
        );
        if (!response.body) throw new Error("Réponse vide du moteur.");
        for await (const event of readEvents(response.body)) {
          switch (event.type) {
            case "start":
              patchLive(userKey, { id: event.user_message_id });
              patchLive(replyKey, { id: event.assistant_message_id });
              if (event.conversation_id !== conversationId) {
                setLiveFor(event.conversation_id);
                onConversation(event.conversation_id);
              }
              break;
            case "context":
              patchLive(replyKey, {
                context: event.items.filter((i) => i.kind !== "historique"),
                stronger: event.stronger ?? null,
              });
              break;
            case "route":
              fallbackFrom = event.fallback_from;
              break;
            case "tool":
              tools = upsertTool(tools, {
                id: event.id,
                name: event.name,
                description: event.description,
                status: event.status,
                ...(event.level === undefined ? {} : { level: event.level }),
                ...(event.error === undefined ? {} : { error: event.error }),
              });
              patchLive(replyKey, { tools });
              break;
            case "plan":
              if (!plans.includes(event.id)) plans.push(event.id);
              patchLive(replyKey, { plans: [...plans] });
              break;
            case "delta":
              content += event.text;
              levelRef.current = Math.min(1, levelRef.current + 0.35);
              setPhase("streaming");
              patchLive(replyKey, { content });
              break;
            case "done":
              patchLive(replyKey, {
                status: "ok",
                meta: {
                  provider: event.provider,
                  model: event.model,
                  latencyMs: event.latency_ms,
                  firstTokenMs: event.first_token_ms,
                  costUsd: event.cost_usd,
                  traceId: event.trace_id,
                  fallbackFrom,
                },
              });
              break;
            case "error":
              setFailed(true);
              patchLive(replyKey, { status: "error", error: event.message });
              break;
          }
        }
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") {
          patchLive(replyKey, { status: "interrupted" });
        } else {
          setFailed(true);
          patchLive(replyKey, { status: "error", error: describeError(error) });
        }
      } finally {
        abortRef.current = null;
        setPhase("idle");
        void queryClient.invalidateQueries({ queryKey: ["conversations"] });
      }
    },
    [client, conversationId, liveFor, onConversation, patchLive, phase, queryClient],
  );

  const stop = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  const reset = useCallback(() => {
    abortRef.current?.abort();
    setLive([]);
    setFailed(false);
  }, []);

  // Le niveau retombe doucement entre deux morceaux de texte.
  const level = useCallback(() => {
    levelRef.current *= 0.93;
    return levelRef.current;
  }, []);

  let sphere: SphereState | null = null;
  if (phase === "waiting") sphere = "thinking";
  else if (phase === "streaming") sphere = "speaking";
  else if (failed) sphere = "error";

  return {
    items,
    phase,
    sphere,
    level,
    send,
    stop,
    reset,
    loading: conversationId !== null && detail.isPending,
    loadError: detail.isError ? describeError(detail.error) : null,
    title: detail.data?.title ?? null,
  };
}
