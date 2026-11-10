// Conversations, mémoire, modèles et le flux d'une réponse.

import type { PlanSummary, ToolActivity } from "./actions";

export interface Conversation {
  id: string;
  title: string;
  channel: "app" | "voice" | "telegram";
  created_at: string;
  updated_at: string;
  message_count: number;
  preview: string;
}

export interface StoredMessage {
  id: number;
  role: "user" | "assistant";
  content: string;
  status: "ok" | "error" | "interrupted" | "streaming";
  created_at: string;
  provider: string | null;
  model: string | null;
  tokens_in: number | null;
  tokens_out: number | null;
  cost_usd: number | null;
  latency_ms: number | null;
  trace_id: string | null;
  /** Outils utilisés et plans proposés pendant ce tour. */
  meta?: { tools?: ToolActivity[]; plans?: string[] };
}

export interface ConversationDetail extends Conversation {
  messages: StoredMessage[];
}

export type MemoryKind = "fait" | "preference" | "profil" | "impasse" | "machine";

export interface MemoryItem {
  id: number;
  kind: MemoryKind;
  content: string;
  source: "conversation" | "manuel";
  confidence: number;
  pinned: boolean;
  created_at: string;
  updated_at: string;
  last_used_at: string | null;
  use_count: number;
  /** Santé, argent, adresse… : envoyé à Jarvis seulement quand la question en parle. */
  personnel: boolean;
  /** Le jour dont parle le souvenir (AAAA-MM-JJ), s'il est dit. */
  event_date: string | null;
  /** Un souvenir de quelques jours s'efface seul à ce moment (jamais s'il est épinglé). */
  expires_at: string | null;
}

export interface ProviderStatus {
  provider: string;
  configured: boolean;
  local: boolean;
  available: boolean;
  degraded: boolean;
  last_error: string | null;
}

export interface ModelInfo {
  provider: string;
  model: string;
  label: string;
  tools: boolean;
  vision: boolean;
  local: boolean;
  price_in: number | null;
  price_out: number | null;
}

export interface ContextItemSummary {
  kind: string;
  preview: string;
  kept: boolean;
}

export type ChatStreamEvent =
  | {
      type: "start";
      conversation_id: string;
      user_message_id: number;
      assistant_message_id: number;
    }
  | { type: "context"; budget: number; used: number; items: ContextItemSummary[] }
  | { type: "route"; provider: string; model: string; fallback_from: string | null }
  | { type: "delta"; text: string }
  /** Le modèle commence un appel d'outil (ses arguments, parfois un fichier entier, suivent). */
  | { type: "writing"; tool: string }
  | ({ type: "tool" } & ToolActivity)
  | ({ type: "plan" } & PlanSummary)
  | {
      type: "done";
      conversation_id: string;
      message_id: number;
      provider: string | null;
      model: string | null;
      tokens_in: number;
      tokens_out: number;
      cost_usd: number | null;
      latency_ms: number | null;
      first_token_ms: number | null;
      trace_id: string;
      plans?: string[];
    }
  | { type: "error"; message: string; partial?: boolean };
