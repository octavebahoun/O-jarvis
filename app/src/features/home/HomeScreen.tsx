import { MessageSquarePlus } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router";

import { Button } from "../../components/Button";
import { ErrorState, LoadingState } from "../../components/States";
import {
  usePlans,
  usePreferences,
  useStatus,
  useVoiceListen,
  useVoiceStatus,
  useVoiceStop,
} from "../../lib/engine/queries";
import { useEngineOnline, useEngineStore } from "../../lib/engine/store";
import type { VoicePhase } from "../../lib/engine/types";
import { useReducedMotion } from "../../lib/motion";
import { useVoiceStore, voiceLevel } from "../../lib/voice";
import styles from "../chat/Chat.module.css";
import { ChatMessage } from "../chat/ChatMessage";
import { Composer } from "../chat/Composer";
import { useChat } from "../chat/useChat";
import { createDemoLevel } from "../sphere/demoLevel";
import { Sphere } from "../sphere/Sphere";
import { SPHERE_STATES, STATE_LABELS, type SphereState } from "../sphere/states";
import { usePerfProbe } from "../sphere/usePerfProbe";
import { useAvatarStore } from "../sphere/avatarStore";
import home from "./HomeScreen.module.css";
import { Suggestions } from "./Suggestions";

function greeting(date: Date): string {
  const hour = date.getHours();
  if (hour < 5 || hour >= 18) return "Bonsoir";
  if (hour < 12) return "Bonjour";
  return "Bon après-midi";
}

const SUBTITLES: Record<SphereState, string> = {
  idle: "Jarvis est prêt.",
  listening: "Jarvis t'écoute.",
  thinking: "Démarrage de Jarvis…",
  speaking: "Jarvis parle.",
  awaiting: "Une action attend ta validation.",
  error: "Le moteur ne répond pas. Nouvel essai en cours…",
};

const VOICE_SPHERE: Partial<Record<VoicePhase, SphereState>> = {
  listening: "listening",
  thinking: "thinking",
  speaking: "speaking",
};

const VOICE_ACTIVE: VoicePhase[] = ["listening", "thinking", "speaking"];

/** Phrase sous le titre de l'accueil, selon la voix. */
function idleSubtitle(live: SphereState, voice: VoicePhase): string {
  if (live !== "idle") return SUBTITLES[live];
  if (voice === "idle") return "Dis « Hey Jarvis », ou écris-moi.";
  if (voice === "starting") return "Jarvis est prêt. La voix se prépare…";
  if (voice === "error") return "Jarvis est prêt. La voix est indisponible.";
  return SUBTITLES.idle;
}

/** État de la sphère quand rien ne se passe dans la conversation. */
function useLiveSphereState(): SphereState {
  const phase = useEngineStore((s) => s.phase);
  const stream = useEngineStore((s) => s.stream);
  if (phase === "failed") return "error";
  if (phase === "booting") return "thinking";
  return stream === "closed" ? "error" : "idle";
}

export function HomeScreen() {
  const [params, setParams] = useSearchParams();
  const conversationId = params.get("c");
  const perfProbe = useEngineStore((s) => s.perfProbe) || params.has("perf");
  const status = useStatus();
  const preferences = usePreferences();
  const reducedMotion = useReducedMotion();
  const online = useEngineOnline();
  const live = useLiveSphereState();
  const pending = usePlans("pending");
  const waitingPlans = pending.data?.length ?? 0;
  const [preview, setPreview] = useState<SphereState | null>(null);
  const { status: probe, onFrame } = usePerfProbe(perfProbe);
  const demoLevel = useMemo(() => createDemoLevel(), []);

  const openConversation = useCallback(
    (id: string) => {
      setParams({ c: id }, { replace: true });
    },
    [setParams],
  );
  const chat = useChat(conversationId, openConversation);

  const voicePhase = useVoiceStore((s) => s.phase);
  const heard = useVoiceStore((s) => s.transcript);
  const saying = useVoiceStore((s) => s.reply);
  const voiceConversation = useVoiceStore((s) => s.conversationId);
  const setVoicePhase = useVoiceStore((s) => s.setPhase);
  const voiceStatus = useVoiceStatus();
  const listen = useVoiceListen();
  const stopVoice = useVoiceStop();
  useEffect(() => {
    if (voiceStatus.data?.state) setVoicePhase(voiceStatus.data.state);
  }, [voiceStatus.data, setVoicePhase]);
  // Un tour vocal vient de finir : on montre sa conversation (sauf si on en lit une autre).
  useEffect(() => {
    if (voiceConversation && (conversationId === null || conversationId === voiceConversation))
      setParams({ c: voiceConversation }, { replace: true });
  }, [voiceConversation]); // eslint-disable-line react-hooks/exhaustive-deps
  const voiceSphere = VOICE_SPHERE[voicePhase] ?? null;
  const voiceActive = VOICE_ACTIVE.includes(voicePhase);

  const showPreview = params.has("demo") || status.data?.dev_mode === true;
  // Une action attend ta validation : la sphère le montre quand rien d'autre ne se passe.
  const resting: SphereState = live === "idle" && waitingPlans > 0 ? "awaiting" : live;
  const state: SphereState = probe.current ?? preview ?? chat.sphere ?? voiceSphere ?? resting;
  const usesDemoLevel = preview === "listening" || preview === "speaking" || probe.current !== null;
  const level = usesDemoLevel
    ? demoLevel
    : chat.sphere === "speaking"
      ? chat.level
      : voiceSphere === "listening" || voiceSphere === "speaking"
        ? voiceLevel
        : undefined;
  const conversing = conversationId !== null || chat.items.length > 0;

  // Défile le fil lui-même (pas scrollIntoView, qui déplacerait le point de départ du
  // focus clavier et ferait sauter le lien d'évitement et la navigation).
  const threadRef = useRef<HTMLDivElement>(null);
  const lastContent = chat.items.at(-1)?.content;
  useEffect(() => {
    const thread = threadRef.current;
    if (!thread || chat.items.length === 0) return;
    thread.scrollTo({ top: thread.scrollHeight, behavior: reducedMotion ? "auto" : "smooth" });
  }, [chat.items.length, lastContent, reducedMotion]);

  const newConversation = () => {
    chat.reset();
    setParams({}, { replace: true });
  };

  const avatarModel = useAvatarStore((s) => s.avatarModel);

  return (
    <div className={home.home} data-mode={conversing ? "conversation" : "accueil"}>
      <section className={home.stage} data-model={avatarModel} aria-label="Jarvis">
        <div className={home.sphere} data-model={avatarModel}>
          <Sphere
            state={state}
            avatarModel={avatarModel}
            {...(level ? { level } : {})}
            quality={preferences.data?.sphere_quality ?? "auto"}
            reducedMotion={reducedMotion}
            onFrame={onFrame}
          />
        </div>
        <div className={home.heading}>
          {conversing ? (
            <h1 className={home.conversationTitle}>{chat.title ?? "Nouvelle conversation"}</h1>
          ) : (
            <h1 className={home.title}>{greeting(new Date())}.</h1>
          )}
          <p className={home.stateLabel} aria-live="polite" data-state={state}>
            {conversing || voiceActive ? STATE_LABELS[state] : idleSubtitle(resting, voicePhase)}
          </p>
        </div>
        {conversing && (
          <Button
            size="sm"
            variant="ghost"
            className={home.newButton}
            icon={<MessageSquarePlus />}
            onClick={newConversation}
          >
            Nouvelle conversation
          </Button>
        )}
      </section>

      {!conversing && !probe.running && (
        <Suggestions
          onOpen={(id) => {
            setParams({ c: id });
          }}
        />
      )}

      {probe.running && (
        <p className={home.probe} role="status">
          Mesure de fluidité en cours : {probe.current ? STATE_LABELS[probe.current] : "…"} (
          {probe.samples.length + 1}/{SPHERE_STATES.length})
        </p>
      )}

      <div className={home.thread} ref={threadRef}>
        {chat.loading && <LoadingState label="Chargement de la conversation…" />}
        {chat.loadError && <ErrorState message={chat.loadError} />}
        <div role="log" aria-label="Conversation">
          <ul className={styles.thread}>
            {chat.items.map((item) => (
              <ChatMessage key={item.key} item={item} />
            ))}
          </ul>
        </div>
      </div>

      <div className={home.bottom}>
        {showPreview && !conversing && !probe.running && (
          <div
            className={home.previewButtons}
            role="group"
            aria-label="Aperçu des états de la sphère"
          >
            <button
              type="button"
              className={home.chip}
              aria-pressed={preview === null}
              onClick={() => {
                setPreview(null);
              }}
            >
              Réel
            </button>
            {SPHERE_STATES.map((s) => (
              <button
                key={s}
                type="button"
                className={home.chip}
                aria-pressed={preview === s}
                onClick={() => {
                  setPreview(s);
                }}
              >
                {STATE_LABELS[s]}
              </button>
            ))}
          </div>
        )}
        {voiceActive && heard && (
          <div className={styles.captions} role="group" aria-label="Sous-titres">
            <p className={styles.heard} aria-live="polite">
              Tu as dit : « {heard} »
            </p>
            {saying && <p className={styles.saying}>{saying}</p>}
          </div>
        )}
        <Composer
          busy={chat.phase !== "idle"}
          disabled={!online}
          onSend={(text) => {
            setPreview(null);
            void chat.send(text);
          }}
          onStop={chat.stop}
          voice={
            voicePhase === "off" || voicePhase === "error" || voicePhase === "starting"
              ? undefined
              : {
                  active: voiceActive,
                  onListen: () => {
                    listen.mutate();
                  },
                  onStop: () => {
                    stopVoice.mutate();
                  },
                }
          }
        />
      </div>
    </div>
  );
}
