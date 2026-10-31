import { useId } from "react";

import { useShortcutStore } from "../../lib/desktop";
import { describeError } from "../../lib/engine/client";
import {
  useCartesiaVoices,
  usePreferences,
  useUpdatePreferences,
  useVoiceDevices,
  useVoiceStatus,
} from "../../lib/engine/queries";
import type { Preferences, VoiceStatus } from "../../lib/engine/types";
import { Choice, PreferenceSwitch } from "./PreferenceControls";
import styles from "./SettingsScreen.module.css";
import voiceStyles from "./VoiceSection.module.css";

const STATE_LABEL: Record<VoiceStatus["state"], string> = {
  off: "Coupée",
  starting: "Préparation…",
  idle: "À l'écoute de « Hey Jarvis »",
  listening: "J'écoute",
  thinking: "Je réfléchis",
  speaking: "Je parle",
  error: "Indisponible",
};

const MODEL_LABEL: Record<string, string> = {
  reveil: "Mot de réveil",
  transcription: "Transcription",
  voix_locale: "Voix locale",
  voix_meta: "Voix de Meta (MMS)",
  voix_meta_en: "Voix anglaise de Meta (MMS)",
};

function statusTone(status: VoiceStatus): "ok" | "down" | "idle" {
  if (status.error || status.state === "error") return "down";
  if (status.state === "off" || status.state === "starting") return "idle";
  return "ok";
}

function VoiceState() {
  const status = useVoiceStatus();
  const preferences = usePreferences();
  if (!status.data?.state) return null;
  const data = status.data;
  // « Tout Meta » : pas de mot de réveil, on parle après le raccourci ou le bouton micro.
  const meta = preferences.data?.model_profile === "meta";
  const title =
    meta && data.state === "idle"
      ? "En veille : parle après le raccourci ou le bouton micro"
      : STATE_LABEL[data.state];
  const models = Object.entries(data.models)
    .map(([key, value]) => `${MODEL_LABEL[key] ?? key} : ${value}`)
    .join(" · ");
  return (
    <div className={voiceStyles.voiceStatus} data-tone={statusTone(data)} role="status">
      <span className={voiceStyles.voiceDot} aria-hidden="true" />
      <div className={voiceStyles.voiceStatusText}>
        <p className={voiceStyles.voiceStatusTitle}>{title}</p>
        {data.error && <p className={styles.radioHint}>{data.error}</p>}
        {models && <p className={styles.radioHint}>{models}</p>}
        <p className={styles.radioHint}>
          {data.echo_cancel
            ? "Annulation d'écho active : tu peux couper Jarvis en parlant."
            : meta
              ? "Sans annulation d'écho : le raccourci coupe Jarvis."
              : "Sans annulation d'écho : redis « Hey Jarvis » pour couper Jarvis."}
          {data.last_latency_ms !== null &&
            ` Dernière réponse parlée en ${(data.last_latency_ms / 1000).toFixed(1).replace(".", ",")} s.`}
        </p>
        {data.tts_fallback && (
          <p className={styles.radioHint}>
            {meta
              ? "Voix du système utilisée : MMS ne répondait pas."
              : "Voix locale utilisée : Cartesia ne répondait pas."}
          </p>
        )}
        {data.stt_fallback && (
          <p className={styles.radioHint}>
            Transcription locale utilisée : Cartesia ne répondait pas.
          </p>
        )}
        <ShortcutLine />
      </div>
    </div>
  );
}

function ShortcutLine() {
  const status = useShortcutStore((s) => s.status);
  const text =
    status.state === "pret"
      ? `Raccourci global : ${status.trigger ?? "à choisir dans les réglages d'Ubuntu"}.`
      : status.state === "demande"
        ? "Raccourci global : en attente de ta confirmation (fenêtre d'Ubuntu)."
        : status.state === "indisponible"
          ? "Raccourci global indisponible ; le bouton micro de l'accueil fonctionne."
          : null;
  return text ? <p className={styles.radioHint}>{text}</p> : null;
}

function DeviceSelect({
  name,
  label,
  options,
}: {
  name: "voice_input_device" | "voice_output_device" | "voice_name";
  label: string;
  options: { value: string; label: string }[];
}) {
  const preferences = usePreferences();
  const update = useUpdatePreferences();
  const id = useId();
  const value = preferences.data?.[name] ?? "";
  const known = options.some((o) => o.value === value);
  return (
    <div className={styles.routingRow}>
      <label htmlFor={id} className={styles.routingLabel}>
        <span>{label}</span>
      </label>
      <select
        id={id}
        className={`${styles.input} ${styles.select}`}
        value={value}
        disabled={!preferences.data || update.isPending}
        onChange={(event) => {
          const next = event.target.value;
          const change: Partial<Preferences> =
            name === "voice_name" ? { voice_name: next } : { [name]: next || null };
          update.mutate(change);
        }}
      >
        {name !== "voice_name" && <option value="">Par défaut du système</option>}
        {!known && value && <option value={value}>{value}</option>}
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </div>
  );
}

export function VoiceSection() {
  const preferences = usePreferences();
  const enabled = preferences.data?.voice_enabled ?? false;
  const meta = preferences.data?.model_profile === "meta";
  const devices = useVoiceDevices(enabled);
  const voices = useCartesiaVoices(enabled && !meta && preferences.data?.voice_engine !== "piper");
  return (
    <>
      <VoiceState />
      <PreferenceSwitch
        name="voice_enabled"
        label="Parler à Jarvis"
        hint={
          meta
            ? "Le micro s'ouvre au raccourci ou au bouton micro : aucun son ne sort avant."
            : "« Hey Jarvis » est reconnu sur ta machine : aucun son n'en sort avant."
        }
      />
      {enabled && meta && (
        <p className={styles.radioHint}>
          « Tout Meta » : tu parles après le raccourci, le bouton micro ou « /voix » dans le
          terminal. Muse Voice Transcribe écoute, MMS (Meta) répond, et espeak-ng, la voix du
          système, prend le relais si MMS ne répond pas. Le mot de réveil, Groq, Cartesia et Whisper
          reviennent en profil « Automatique » (Réglages › Modèles).
        </p>
      )}
      {enabled && (
        <>
          {!meta && (
            <>
              <Choice
                name="wake_sensitivity"
                label="Sensibilité du mot de réveil"
                options={[
                  { value: "basse", label: "Basse", hint: "Moins de faux réveils." },
                  {
                    value: "normale",
                    label: "Normale",
                    hint: "Un doute est vérifié par transcription locale.",
                  },
                  { value: "haute", label: "Haute", hint: "Pour une voix lointaine ou un accent." },
                ]}
              />
              <Choice
                name="voice_stt"
                label="Transcription"
                options={[
                  {
                    value: "groq",
                    label: "Groq (en ligne)",
                    hint: "Whisper large-v3, français imposé. Ta phrase part en ligne, jamais avant « Hey Jarvis ».",
                  },
                  {
                    value: "cartesia",
                    label: "Cartesia (en ligne)",
                    hint: "En direct, mais il devine la langue seul et peut entendre de l'anglais.",
                  },
                  {
                    value: "local",
                    label: "Locale (Whisper)",
                    hint: "Tout reste sur la machine, mais elle comprend beaucoup moins bien.",
                  },
                ]}
              />
              <Choice
                name="voice_engine"
                label="Voix de Jarvis"
                options={[
                  {
                    value: "auto",
                    label: "Cartesia",
                    hint: "Naturelle ; la voix locale prend le relais hors ligne.",
                  },
                  { value: "piper", label: "Locale (Piper)", hint: "Tout reste sur la machine." },
                ]}
              />
              {preferences.data?.voice_engine !== "piper" &&
                voices.data &&
                voices.data.length > 0 && (
                  <DeviceSelect
                    name="voice_name"
                    label="Timbre"
                    options={voices.data.map((v) => ({
                      value: v.id,
                      label: v.description ? `${v.name} — ${v.description}` : v.name,
                    }))}
                  />
                )}
            </>
          )}
          {devices.data && (
            <>
              <DeviceSelect
                name="voice_input_device"
                label="Micro"
                options={devices.data.inputs.map((d) => ({ value: d.name, label: d.label }))}
              />
              <DeviceSelect
                name="voice_output_device"
                label="Haut-parleur"
                options={devices.data.outputs.map((d) => ({ value: d.name, label: d.label }))}
              />
            </>
          )}
          {devices.isError && (
            <p className={styles.ko} role="alert">
              {describeError(devices.error)}
            </p>
          )}
          <PreferenceSwitch
            name="voice_follow_up"
            label={meta ? "Continuer sans rappuyer" : "Continuer sans redire « Hey Jarvis »"}
            hint="Après une réponse, Jarvis écoute encore six secondes."
          />
          <PreferenceSwitch
            name="voice_barge_in"
            label="Couper Jarvis en parlant"
            hint="Avec l'annulation d'écho ou un casque."
          />
          <PreferenceSwitch
            name="voice_headset"
            label="J'utilise un casque"
            hint="Jarvis ne peut pas s'entendre : la coupure à la voix reste possible."
          />
        </>
      )}
    </>
  );
}
