import {
  AlertCircle,
  BrainCircuit,
  Check,
  ChevronDown,
  ClipboardList,
  LoaderCircle,
  Sparkles,
  XCircle,
} from "lucide-react";
import { memo, useId, useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { ExternalLink } from "../../components/RichText";
import type { ToolActivity } from "../../lib/engine/types";
import { formatDuration } from "../../lib/format";
import actions from "../actions/Actions.module.css";
import { PlanCard } from "../actions/PlanCard";
import styles from "./Chat.module.css";
import type { ChatItem } from "./useChat";

const KIND_LABEL: Record<string, string> = {
  profil: "profil",
  fait: "fait",
  preference: "préférence",
  souvenir: "échange passé",
  impasse: "impasse",
  machine: "carte de la machine",
};

function formatCost(cost: number | null): string | null {
  if (cost === null) return null;
  if (cost === 0) return "gratuit";
  if (cost < 0.001) return "< 0,001 $";
  return `${cost.toFixed(3).replace(".", ",")} $`;
}

function ContextDisclosure({ item }: { item: ChatItem }) {
  const [open, setOpen] = useState(false);
  const panelId = useId();
  const used = (item.context ?? []).filter((c) => c.kept);
  if (used.length === 0) return null;
  return (
    <div className={styles.context}>
      <button
        type="button"
        className={styles.contextToggle}
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => {
          setOpen((v) => !v);
        }}
      >
        <BrainCircuit aria-hidden="true" />
        {used.length === 1 ? "1 souvenir utilisé" : `${used.length} souvenirs utilisés`}
        <ChevronDown className={styles.chevron} data-open={open} aria-hidden="true" />
      </button>
      {open && (
        <ul id={panelId} className={styles.contextList}>
          {used.map((c) => (
            <li key={`${c.kind}-${c.preview}`}>
              <span className={styles.contextKind}>{KIND_LABEL[c.kind] ?? c.kind}</span>
              {c.preview}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function ToolIcon({ status }: { status: ToolActivity["status"] }) {
  if (status === "running") return <LoaderCircle aria-hidden="true" />;
  if (status === "failed") return <XCircle aria-hidden="true" />;
  if (status === "proposed") return <ClipboardList aria-hidden="true" />;
  return <Check aria-hidden="true" />;
}

const TOOL_STATUS: Record<ToolActivity["status"], string> = {
  running: "en cours",
  done: "fait",
  failed: "en échec",
  proposed: "plan proposé",
};

/** Ce que Jarvis a fait pendant le tour : une ligne par outil. */
function ToolActivityList({ tools }: { tools: ToolActivity[] }) {
  const shown = tools.filter((t) => t.status !== "proposed");
  if (shown.length === 0) return null;
  return (
    <ul className={actions.tools} aria-label="Outils utilisés">
      {shown.map((tool) => (
        <li key={tool.id} className={actions.tool} data-status={tool.status}>
          <ToolIcon status={tool.status} />
          <span className={actions.toolText} title={tool.error ?? tool.description}>
            {tool.description}
            <span className="visually-hidden"> : {TOOL_STATUS[tool.status]}</span>
            {tool.error ? ` — ${tool.error}` : ""}
          </span>
        </li>
      ))}
    </ul>
  );
}

/** Il propose, tu choisis : refaire la réponse avec un modèle plus fort (jamais tout seul). */
function StrongerOffer({
  item,
  onStronger,
}: {
  item: ChatItem;
  onStronger?: (model: string) => void;
}) {
  const stronger = item.stronger;
  const answeredBy =
    item.meta?.provider && item.meta.model ? `${item.meta.provider}/${item.meta.model}` : null;
  if (!stronger || !onStronger || item.status !== "ok" || answeredBy === stronger.model)
    return null;
  return (
    <button
      type="button"
      className={styles.stronger}
      onClick={() => {
        onStronger(stronger.model);
      }}
    >
      <Sparkles aria-hidden="true" />
      Tâche lourde : refaire avec {stronger.label}
    </button>
  );
}

export const ChatMessage = memo(function ChatMessage({
  item,
  onStronger,
}: {
  item: ChatItem;
  /** Refaire la réponse avec ce modèle (« fournisseur/modèle »). */
  onStronger?: (model: string) => void;
}) {
  if (item.role === "user") {
    return (
      <li className={styles.user}>
        <p className={styles.userBubble}>{item.content}</p>
      </li>
    );
  }
  const streaming = item.status === "streaming";
  const meta = item.meta;
  const details = [
    meta?.model,
    meta?.latencyMs ? formatDuration(meta.latencyMs) : null,
    formatCost(meta?.costUsd ?? null),
  ].filter(Boolean);
  return (
    <li className={styles.assistant} aria-busy={streaming}>
      <span className="visually-hidden">Jarvis :</span>
      <ToolActivityList tools={item.tools} />
      {item.content ? (
        <div className={styles.markdown}>
          <Markdown remarkPlugins={[remarkGfm]} components={{ a: ExternalLink }}>
            {item.content}
          </Markdown>
        </div>
      ) : (
        streaming && (
          <p className={styles.waiting} role="status">
            Jarvis réfléchit…
          </p>
        )
      )}
      {item.status === "interrupted" && <p className={styles.note}>Réponse interrompue.</p>}
      {item.status === "error" && (
        <p className={styles.error} role="alert">
          <AlertCircle aria-hidden="true" />
          {item.error ?? "La réponse n'a pas pu aboutir."}
        </p>
      )}
      {meta?.fallbackFrom && (
        <p className={styles.note}>
          {meta.fallbackFrom.split("/")[0]} ne répondait pas : réponse de {meta.provider}.
        </p>
      )}
      {item.plans.map((id) => (
        <PlanCard key={id} id={id} showSynthesis={false} />
      ))}
      <ContextDisclosure item={item} />
      <StrongerOffer item={item} onStronger={onStronger} />
      {details.length > 0 && <p className={styles.meta}>{details.join(" · ")}</p>}
    </li>
  );
});
