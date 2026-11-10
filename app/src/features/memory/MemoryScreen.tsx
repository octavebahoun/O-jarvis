import { Lock, Pin, Plus, Search, Trash2 } from "lucide-react";
import { Tabs } from "radix-ui";
import { useDeferredValue, useId, useState, type SyntheticEvent } from "react";

import { Button } from "../../components/Button";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { PageHeader } from "../../components/PageHeader";
import { EmptyState, ErrorState, LoadingState, OfflineState } from "../../components/States";
import { describeError } from "../../lib/engine/client";
import {
  useAddMemory,
  useDeleteMemory,
  useMemory,
  useUpdateMemory,
} from "../../lib/engine/queries";
import { useEngineOnline } from "../../lib/engine/store";
import type { MemoryItem, MemoryKind } from "../../lib/engine/types";
import { formatRelative } from "../../lib/format";
import { MemoryGraphView } from "./MemoryGraph";
import styles from "./MemoryScreen.module.css";

const KINDS: { value: MemoryKind | "tout"; label: string }[] = [
  { value: "tout", label: "Tout" },
  { value: "profil", label: "Profil" },
  { value: "preference", label: "Préférences" },
  { value: "fait", label: "Faits" },
  { value: "impasse", label: "Impasses" },
  { value: "machine", label: "Machine" },
];

const KIND_LABEL: Record<MemoryKind, string> = {
  profil: "Profil",
  preference: "Préférence",
  fait: "Fait",
  impasse: "Impasse",
  machine: "Machine",
};

function MemoryRow({ item }: { item: MemoryItem }) {
  const update = useUpdateMemory();
  const remove = useDeleteMemory();
  const [editing, setEditing] = useState(false);
  const [content, setContent] = useState(item.content);
  const fieldId = useId();

  const save = (event: SyntheticEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!content.trim()) return;
    update.mutate(
      { id: item.id, content },
      {
        onSuccess: () => {
          setEditing(false);
        },
      },
    );
  };

  return (
    <li className={styles.item} data-pinned={item.pinned}>
      <span className={styles.kind}>{KIND_LABEL[item.kind]}</span>
      {editing ? (
        <form className={styles.editForm} onSubmit={save}>
          <label htmlFor={fieldId} className="visually-hidden">
            Modifier ce souvenir
          </label>
          <textarea
            id={fieldId}
            className={styles.textarea}
            value={content}
            maxLength={1000}
            rows={2}
            onChange={(e) => {
              setContent(e.target.value);
            }}
          />
          <div className={styles.editActions}>
            <Button type="submit" size="sm" variant="primary" busy={update.isPending}>
              Enregistrer
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                setEditing(false);
                setContent(item.content);
              }}
            >
              Annuler
            </Button>
          </div>
        </form>
      ) : (
        <button
          type="button"
          className={styles.content}
          title="Modifier"
          onClick={() => {
            setEditing(true);
          }}
        >
          {item.content}
        </button>
      )}
      <p className={styles.meta}>
        {item.source === "conversation" ? "Appris en conversation" : "Ajouté à la main"} ·{" "}
        {formatRelative(item.updated_at)}
        {item.use_count > 0 && ` · utilisé ${item.use_count} fois`}
        {item.pinned && " · épinglé"}
        {item.personnel && " · personnel"}
      </p>
      <div className={styles.actions}>
        <Button
          size="sm"
          variant="ghost"
          className={styles.pin}
          aria-pressed={item.pinned}
          aria-label="Épingler (toujours envoyé à Jarvis)"
          icon={<Pin />}
          onClick={() => {
            update.mutate({ id: item.id, pinned: !item.pinned });
          }}
        />
        <Button
          size="sm"
          variant="ghost"
          className={styles.pin}
          aria-pressed={item.personnel}
          aria-label="Personnel (envoyé à Jarvis seulement si ta question en parle)"
          title="Personnel : envoyé seulement si ta question en parle"
          icon={<Lock />}
          onClick={() => {
            update.mutate({ id: item.id, personnel: !item.personnel });
          }}
        />
        <ConfirmDialog
          trigger={
            <Button size="sm" variant="ghost" aria-label="Oublier ce souvenir" icon={<Trash2 />} />
          }
          title="Oublier ce souvenir ?"
          description={`Jarvis ne saura plus que : « ${item.content} »`}
          confirmLabel="Oublier"
          onConfirm={() => {
            remove.mutate(item.id);
          }}
        />
      </div>
      {(update.error ?? remove.error) && (
        <p className={styles.error} role="alert">
          {describeError(update.error ?? remove.error)}
        </p>
      )}
    </li>
  );
}

function AddMemory() {
  const add = useAddMemory();
  const [kind, setKind] = useState<MemoryKind>("fait");
  const [content, setContent] = useState("");
  const contentId = useId();
  const kindId = useId();

  const submit = (event: SyntheticEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!content.trim()) return;
    add.mutate(
      { kind, content },
      {
        onSuccess: () => {
          setContent("");
        },
      },
    );
  };

  return (
    <form className={styles.add} onSubmit={submit}>
      <label htmlFor={kindId} className="visually-hidden">
        Type de souvenir
      </label>
      <select
        id={kindId}
        className={styles.select}
        value={kind}
        onChange={(e) => {
          setKind(e.target.value as MemoryKind);
        }}
      >
        <option value="fait">Fait</option>
        <option value="preference">Préférence</option>
        <option value="profil">Profil</option>
      </select>
      <label htmlFor={contentId} className="visually-hidden">
        Ce que Jarvis doit retenir
      </label>
      <input
        id={contentId}
        className={styles.input}
        placeholder="Ex. : Je préfère les réponses courtes le matin."
        value={content}
        maxLength={1000}
        onChange={(e) => {
          setContent(e.target.value);
        }}
      />
      <Button type="submit" variant="primary" size="sm" icon={<Plus />} busy={add.isPending}>
        Retenir
      </Button>
      {add.error && (
        <p className={styles.error} role="alert">
          {describeError(add.error)}
        </p>
      )}
    </form>
  );
}

export function MemoryScreen() {
  const [view, setView] = useState<"liste" | "graphe">("liste");
  const [tab, setTab] = useState<MemoryKind | "tout">("tout");
  const [query, setQuery] = useState("");
  const deferred = useDeferredValue(query);
  const memory = useMemory(tab === "tout" ? null : tab, deferred);
  const online = useEngineOnline();
  const searchId = useId();

  let content;
  if (!online && !memory.data) content = <OfflineState what="La mémoire" />;
  else if (memory.isPending) content = <LoadingState label="Lecture de la mémoire…" />;
  else if (memory.isError)
    content = (
      <ErrorState
        message={describeError(memory.error)}
        onRetry={() => {
          void memory.refetch();
        }}
      />
    );
  else if (memory.data.length === 0)
    content = deferred ? (
      <EmptyState title="Aucun résultat">Rien ne correspond à « {deferred} ».</EmptyState>
    ) : (
      <EmptyState title="Jarvis ne sait encore rien ici">
        Parle-lui de toi, ou ajoute un souvenir ci-dessus : il s'en servira dans les conversations
        suivantes.
      </EmptyState>
    );
  else
    content = (
      <ul className={styles.list} aria-label="Souvenirs">
        {memory.data.map((item) => (
          <MemoryRow key={item.id} item={item} />
        ))}
      </ul>
    );

  return (
    <div className={styles.page}>
      <PageHeader
        title="Mémoire"
        description="Ce que Jarvis sait de toi. Tout est modifiable ; rien ne quitte ta machine pour être indexé."
      />
      <Tabs.Root
        value={view}
        onValueChange={(value) => {
          setView(value as "liste" | "graphe");
        }}
      >
        <Tabs.List className={styles.views} aria-label="Vue de la mémoire">
          <Tabs.Trigger value="liste" className={styles.tab}>
            Souvenirs
          </Tabs.Trigger>
          <Tabs.Trigger value="graphe" className={styles.tab}>
            Graphe
          </Tabs.Trigger>
        </Tabs.List>
        <Tabs.Content value="graphe" className={styles.panel}>
          {view === "graphe" && <MemoryGraphView />}
        </Tabs.Content>
        <Tabs.Content value="liste" className={styles.panel}>
          <AddMemory />
          <Tabs.Root
            value={tab}
            onValueChange={(value) => {
              setTab(value as MemoryKind | "tout");
            }}
          >
            <div className={styles.toolbar}>
              <Tabs.List className={styles.tabs} aria-label="Catégories de souvenirs">
                {KINDS.map((k) => (
                  <Tabs.Trigger key={k.value} value={k.value} className={styles.tab}>
                    {k.label}
                  </Tabs.Trigger>
                ))}
              </Tabs.List>
              <div className={styles.search}>
                <Search aria-hidden="true" />
                <label htmlFor={searchId} className="visually-hidden">
                  Rechercher dans la mémoire
                </label>
                <input
                  id={searchId}
                  type="search"
                  className={styles.searchInput}
                  placeholder="Rechercher…"
                  value={query}
                  onChange={(e) => {
                    setQuery(e.target.value);
                  }}
                />
              </div>
            </div>
            {KINDS.map((k) => (
              <Tabs.Content key={k.value} value={k.value} className={styles.panel}>
                {tab === k.value && content}
              </Tabs.Content>
            ))}
          </Tabs.Root>
        </Tabs.Content>
      </Tabs.Root>
    </div>
  );
}
