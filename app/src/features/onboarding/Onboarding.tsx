import { CircleCheck, CircleDashed, Copy } from "lucide-react";
import { Dialog } from "radix-ui";
import { useState, type ReactNode } from "react";

import { Button } from "../../components/Button";
import { describeError } from "../../lib/engine/client";
import {
  useLocalModels,
  usePreferences,
  useRetryLocalModels,
  useSecrets,
  useToggleScreenShare,
  useUpdatePreferences,
} from "../../lib/engine/queries";
import { useEngineStore } from "../../lib/engine/store";
import { SecretRow } from "../settings/SecretRow";
import { useAuthorizePointer, useChromeStatus } from "./hooks";
import { useOnboarding } from "./store";
import styles from "./Onboarding.module.css";

/** Le chemin de l'extension Chrome installée par le paquet .deb. */
const EXTENSION_PATH = "/usr/lib/O-jarvis/extension";

/**
 * L'accueil du premier lancement : tout ce que Jarvis demandera, demandé ici d'un coup, pour
 * qu'aucune autorisation ne surgisse en pleine démonstration. Chaque étape dit pourquoi,
 * propose son bouton, puis montre « fait ». On peut tout passer et y revenir (Réglages ›
 * Démarrage).
 */
export function Onboarding() {
  const open = useOnboarding((s) => s.open);
  const finish = useOnboarding((s) => s.finish);
  const ready = useEngineStore((s) => s.phase === "ready" && s.client !== null);
  return (
    <Dialog.Root
      open={open && ready}
      onOpenChange={(next) => {
        if (!next) finish();
      }}
    >
      <Dialog.Portal>
        <Dialog.Overlay className={styles.overlay} />
        <Dialog.Content className={styles.dialog}>
          <Dialog.Title className={styles.title}>Bienvenue dans O-jarvis</Dialog.Title>
          <Dialog.Description className={styles.lead}>
            Six étapes, une seule fois : Jarvis te demande ici tout ce dont il aura besoin, pour que
            rien ne t'interrompe ensuite.
          </Dialog.Description>
          <ol className={styles.steps}>
            <KeyStep />
            <ProfileStep />
            <PcStep />
            <VoiceStep />
            <ChromeStep open={open} />
            <TerminalStep />
          </ol>
          <div className={styles.actions}>
            <Button variant="ghost" onClick={finish}>
              Passer
            </Button>
            <Button variant="primary" onClick={finish}>
              C'est prêt
            </Button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function Step({ title, done, children }: { title: string; done: boolean; children: ReactNode }) {
  return (
    <li className={styles.step} data-done={done}>
      <h3 className={styles.stepTitle}>
        {done ? (
          <CircleCheck className={styles.ok} aria-label="Fait" />
        ) : (
          <CircleDashed className={styles.todo} aria-label="À faire" />
        )}
        {title}
      </h3>
      <div className={styles.stepBody}>{children}</div>
    </li>
  );
}

function KeyStep() {
  const secrets = useSecrets();
  const key = secrets.data?.find((s) => s.name === "MODEL_API_KEY");
  return (
    <Step title="Ta clé Meta" done={key?.configured ?? false}>
      <p>Elle reste dans le trousseau du système et n'est jamais réaffichée.</p>
      {key && (
        <ul className={styles.secret}>
          <SecretRow secret={key} disabled={false} />
        </ul>
      )}
    </Step>
  );
}

function ProfileStep() {
  const preferences = usePreferences();
  const update = useUpdatePreferences();
  const meta = preferences.data?.model_profile === "meta";
  return (
    <Step title="Le profil « Tout Meta »" done={meta}>
      <p>
        Chaque modèle appelé est un modèle de Meta, secours compris : Muse Spark, Muse Spark Open,
        Muse Wonder, Muse Voice Transcribe, SAM 3.1 et les voix MMS.
      </p>
      {!meta && (
        <Button
          size="sm"
          disabled={update.isPending}
          onClick={() => {
            update.mutate({ model_profile: "meta" });
          }}
        >
          Passer en « Tout Meta »
        </Button>
      )}
    </Step>
  );
}

function PcStep() {
  const preferences = usePreferences();
  const update = useUpdatePreferences();
  const share = useToggleScreenShare();
  const pointer = useAuthorizePointer();
  const [shared, setShared] = useState(false);
  const enabled = preferences.data?.os_control_enabled ?? false;
  const error = share.error ?? pointer.error;
  return (
    <Step title="Le contrôle du PC" done={enabled && shared && pointer.isSuccess}>
      <p>
        Pour regarder l'écran et cliquer dans tes applications. GNOME demande deux accords, une
        seule fois : accepte-les maintenant.
      </p>
      <div className={styles.row}>
        <Button
          size="sm"
          disabled={enabled || update.isPending}
          onClick={() => {
            update.mutate({ os_control_enabled: true });
          }}
        >
          {enabled ? "Contrôle activé" : "1. Activer le contrôle"}
        </Button>
        <Button
          size="sm"
          disabled={!enabled || shared || share.isPending}
          onClick={() => {
            void share
              .mutateAsync(true)
              .then(() => share.mutateAsync(false))
              .then(() => {
                setShared(true);
              });
          }}
        >
          {shared ? "Partage d'écran accepté" : "2. Accord pour l'écran"}
        </Button>
        <Button
          size="sm"
          disabled={!enabled || pointer.isSuccess || pointer.isPending}
          onClick={() => {
            pointer.mutate();
          }}
        >
          {pointer.isSuccess ? "Contrôle à distance accepté" : "3. Accord pour cliquer"}
        </Button>
      </div>
      {error && (
        <p className={styles.error} role="alert">
          {describeError(error)}
        </p>
      )}
    </Step>
  );
}

function VoiceStep() {
  const models = useLocalModels();
  const retry = useRetryLocalModels();
  const voices = (models.data ?? []).filter((m) => m.key.startsWith("voix_meta"));
  const ready = voices.length > 0 && voices.every((m) => m.ready);
  return (
    <Step title="La voix de Meta" done={ready}>
      <p>
        Les voix MMS de Meta, française et anglaise, parlent sur ton PC : environ 230 Mo à
        télécharger une fois. Le micro sera demandé au premier « /voix ».
      </p>
      {voices.length > 0 ? (
        <ul className={styles.list}>
          {voices.map((m) => (
            <li key={m.key}>
              {m.label} : {m.state}
            </li>
          ))}
        </ul>
      ) : (
        <p className={styles.muted}>Les voix de Meta viennent avec O-jarvis Shell.</p>
      )}
      {voices.length > 0 && !ready && (
        <Button
          size="sm"
          disabled={retry.isPending}
          onClick={() => {
            retry.mutate();
          }}
        >
          Télécharger les voix
        </Button>
      )}
    </Step>
  );
}

function ChromeStep({ open }: { open: boolean }) {
  const status = useChromeStatus(open);
  const connected = status.data?.connected ?? false;
  return (
    <Step title="Jarvis dans Chrome" done={connected}>
      <ol className={styles.list}>
        <li>
          Ouvre <code>chrome://extensions</code> et active le <b>Mode développeur</b>.
        </li>
        <li>
          <b>Charger l'extension non empaquetée</b>, Ctrl+L, puis colle ce chemin :{" "}
          <CopyText text={EXTENSION_PATH} />
        </li>
        <li>Épingle l'icône O-jarvis : le panneau s'ouvre à côté de la page.</li>
      </ol>
      <p className={styles.muted}>
        {status.data === null
          ? "L'extension vient avec O-jarvis Shell."
          : connected
            ? "Extension branchée sur ce moteur."
            : "En attente de l'extension…"}
      </p>
    </Step>
  );
}

function TerminalStep() {
  return (
    <Step title="Jarvis dans le terminal" done={false}>
      <p>
        Dans n'importe quel dossier : <CopyText text="o-jarvis-shell" />
      </p>
      <ul className={styles.list}>
        <li>« How much disk space is left? » : il lit tout de suite.</li>
        <li>« Create notes.txt here with a shopping list. » : il attend ton « o ».</li>
        <li>« /modele wonder », puis « Build a one-page site for a tea shop. »</li>
      </ul>
    </Step>
  );
}

function CopyText({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <span className={styles.copy}>
      <code>{text}</code>
      <button
        type="button"
        className={styles.copyButton}
        aria-label={`Copier ${text}`}
        onClick={() => {
          void navigator.clipboard.writeText(text).then(() => {
            setCopied(true);
          });
        }}
      >
        <Copy aria-hidden="true" />
        {copied ? "Copié" : "Copier"}
      </button>
    </span>
  );
}
