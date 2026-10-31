import { RadioGroup } from "radix-ui";

import { PageHeader } from "../../components/PageHeader";
import { ErrorState, LoadingState, OfflineState } from "../../components/States";
import { describeError } from "../../lib/engine/client";
import { useSecrets, useStatus } from "../../lib/engine/queries";
import { useEngineOnline } from "../../lib/engine/store";
import type { SecretGroup, SecretStatus } from "../../lib/engine/types";
import { useAvatarStore } from "../sphere/avatarStore";
import { BrowserSection } from "./BrowserSection";
import { DesktopSection } from "./DesktopSection";
import { IntegrationsSection } from "./IntegrationsSection";
import { LocalModelsSection } from "./LocalModelsSection";
import { ModelsSection } from "./ModelsSection";
import { PermissionsSection } from "./PermissionsSection";
import { Choice } from "./PreferenceControls";
import { SecretRow } from "./SecretRow";
import { StartupSection } from "./StartupSection";
import { VoiceSection } from "./VoiceSection";
import styles from "./SettingsScreen.module.css";

const GROUPS: { id: SecretGroup; title: string; description: string }[] = [
  { id: "llm", title: "Modèles de langage", description: "Le cerveau de Jarvis." },
  {
    id: "voice",
    title: "Voix en ligne",
    description: "Transcription de ta voix (Groq) et voix naturelle de Jarvis (Cartesia).",
  },
  { id: "search", title: "Recherche", description: "Recherche sur le web." },
  { id: "integrations", title: "Intégrations", description: "GitHub, Notion, Google Agenda." },
  { id: "phone", title: "Téléphone", description: "Parler à Jarvis depuis Telegram." },
];

function ProvidersSection() {
  const secrets = useSecrets();
  const status = useStatus();
  const online = useEngineOnline();

  if (!online && !secrets.data) return <OfflineState what="La liste des clés" />;
  if (secrets.isPending) return <LoadingState label="Lecture du trousseau…" />;
  if (secrets.isError) {
    return (
      <ErrorState
        message={describeError(secrets.error)}
        onRetry={() => {
          void secrets.refetch();
        }}
      />
    );
  }

  const keyringError = status.data?.keyring_error ?? null;
  const byGroup = new Map<SecretGroup, SecretStatus[]>();
  for (const secret of secrets.data) {
    byGroup.set(secret.group, [...(byGroup.get(secret.group) ?? []), secret]);
  }

  return (
    <>
      {keyringError && (
        <ErrorState
          title="Trousseau indisponible"
          message={`${keyringError} Les clés ne peuvent pas être enregistrées tant qu'il n'est pas déverrouillé.`}
        />
      )}
      {GROUPS.map((group) => (
        <section key={group.id} className={styles.group} aria-labelledby={`groupe-${group.id}`}>
          <div className={styles.groupHead}>
            <h3 id={`groupe-${group.id}`} className={styles.groupTitle}>
              {group.title}
            </h3>
            <p className={styles.groupDescription}>{group.description}</p>
          </div>
          <ul className={styles.secrets}>
            {(byGroup.get(group.id) ?? []).map((secret) => (
              <SecretRow
                key={secret.name}
                secret={secret}
                disabled={!online || keyringError !== null}
              />
            ))}
          </ul>
        </section>
      ))}
    </>
  );
}

export function SettingsScreen() {
  return (
    <div className={styles.page}>
      <PageHeader
        title="Réglages"
        description="Les clés sont gardées dans le trousseau du système. Une clé enregistrée n'est jamais réaffichée."
      />
      <section className={styles.section} aria-labelledby="fournisseurs">
        <h2 id="fournisseurs" className={styles.sectionTitle}>
          Fournisseurs et clés
        </h2>
        <ProvidersSection />
      </section>
      <section className={styles.section} aria-labelledby="modeles">
        <h2 id="modeles" className={styles.sectionTitle}>
          Modèles
        </h2>
        <ModelsSection />
      </section>
      <section className={styles.section} aria-labelledby="voix">
        <h2 id="voix" className={styles.sectionTitle}>
          Voix
        </h2>
        <VoiceSection />
      </section>
      <section className={styles.section} aria-labelledby="modeles-locaux">
        <h2 id="modeles-locaux" className={styles.sectionTitle}>
          Modèles locaux
        </h2>
        <LocalModelsSection />
      </section>
      <section className={styles.section} aria-labelledby="integrations">
        <h2 id="integrations" className={styles.sectionTitle}>
          Intégrations et téléphone
        </h2>
        <IntegrationsSection />
      </section>
      <section className={styles.section} aria-labelledby="navigateur">
        <h2 id="navigateur" className={styles.sectionTitle}>
          Navigateur et cookies
        </h2>
        <BrowserSection />
      </section>
      <section className={styles.section} aria-labelledby="controle-pc">
        <h2 id="controle-pc" className={styles.sectionTitle}>
          Contrôle du PC
        </h2>
        <DesktopSection />
      </section>
      <section className={styles.section} aria-labelledby="permissions">
        <h2 id="permissions" className={styles.sectionTitle}>
          Permissions
        </h2>
        <PermissionsSection />
      </section>
      <section className={styles.section} aria-labelledby="demarrage">
        <h2 id="demarrage" className={styles.sectionTitle}>
          Démarrage
        </h2>
        <StartupSection />
      </section>
      <section className={styles.section} aria-labelledby="apparence">
        <h2 id="apparence" className={styles.sectionTitle}>
          Apparence
        </h2>
        <Choice
          name="reduced_motion"
          label="Animations"
          options={[
            { value: "system", label: "Comme le système", hint: "Suit le réglage d'Ubuntu." },
            {
              value: "reduce",
              label: "Réduites",
              hint: "Mouvements minimaux, mêmes informations.",
            },
            { value: "full", label: "Complètes", hint: "Toutes les animations." },
          ]}
        />
        <Choice
          name="sphere_quality"
          label="Qualité de la sphère"
          options={[
            { value: "auto", label: "Automatique", hint: "Équilibre fluidité et finesse." },
            { value: "high", label: "Haute", hint: "Plus de points, plus de calcul." },
            { value: "low", label: "Économe", hint: "Pour une machine chargée." },
          ]}
        />
        <AvatarModelChoice />
      </section>
    </div>
  );
}

function AvatarModelChoice() {
  const avatarModel = useAvatarStore((s) => s.avatarModel);
  const setAvatarModel = useAvatarStore((s) => s.setAvatarModel);

  return (
    <div className={styles.choice}>
      <p id="label-avatar-model" className={styles.choiceLabel}>
        Modèle de l'avatar
      </p>
      <RadioGroup.Root
        className={styles.radios}
        aria-labelledby="label-avatar-model"
        value={avatarModel}
        onValueChange={(next) => {
          if (next === "sphere" || next === "cyberMask") {
            setAvatarModel(next);
          }
        }}
      >
        <RadioGroup.Item value="sphere" className={styles.radio}>
          <span className={styles.radioDot} aria-hidden="true">
            <RadioGroup.Indicator className={styles.radioIndicator} />
          </span>
          <span className={styles.radioText}>
            <span>Sphère classique</span>
            <span className={styles.radioHint}>Avatar d'origine en nuage de points dynamique.</span>
          </span>
        </RadioGroup.Item>
        <RadioGroup.Item value="cyberMask" className={styles.radio}>
          <span className={styles.radioDot} aria-hidden="true">
            <RadioGroup.Indicator className={styles.radioIndicator} />
          </span>
          <span className={styles.radioText}>
            <span>Masque cybernétique</span>
            <span className={styles.radioHint}>
              Masque facial stylisé avec pluie binaire Matrix.
            </span>
          </span>
        </RadioGroup.Item>
      </RadioGroup.Root>
    </div>
  );
}
