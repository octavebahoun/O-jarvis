import {
  Brain,
  Globe,
  HardDrive,
  KeyRound,
  type LucideIcon,
  Mic,
  MousePointerClick,
  Palette,
  Plug,
  Power,
  ShieldCheck,
} from "lucide-react";
import { RadioGroup, Tabs } from "radix-ui";
import { type ReactNode, useRef } from "react";
import { useSearchParams } from "react-router";

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
import nav from "./SettingsNav.module.css";
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

/** La section ouverte quand l'adresse n'en demande aucune. */
const FIRST_SECTION = "fournisseurs";

/** Les sections des réglages, dans l'ordre de la barre : une seule s'affiche à la fois. */
const SECTIONS: {
  id: string;
  label: string;
  title: string;
  icon: LucideIcon;
  body: () => ReactNode;
}[] = [
  {
    id: FIRST_SECTION,
    label: "Clés",
    title: "Fournisseurs et clés",
    icon: KeyRound,
    body: () => <ProvidersSection />,
  },
  { id: "modeles", label: "Modèles", title: "Modèles", icon: Brain, body: () => <ModelsSection /> },
  { id: "voix", label: "Voix", title: "Voix", icon: Mic, body: () => <VoiceSection /> },
  {
    id: "modeles-locaux",
    label: "Modèles locaux",
    title: "Modèles locaux",
    icon: HardDrive,
    body: () => <LocalModelsSection />,
  },
  {
    id: "integrations",
    label: "Intégrations",
    title: "Intégrations et téléphone",
    icon: Plug,
    body: () => <IntegrationsSection />,
  },
  {
    id: "navigateur",
    label: "Navigateur",
    title: "Navigateur et cookies",
    icon: Globe,
    body: () => <BrowserSection />,
  },
  {
    id: "controle-pc",
    label: "Contrôle du PC",
    title: "Contrôle du PC",
    icon: MousePointerClick,
    body: () => <DesktopSection />,
  },
  {
    id: "permissions",
    label: "Permissions",
    title: "Permissions",
    icon: ShieldCheck,
    body: () => <PermissionsSection />,
  },
  {
    id: "demarrage",
    label: "Démarrage",
    title: "Démarrage",
    icon: Power,
    body: () => <StartupSection />,
  },
  {
    id: "apparence",
    label: "Apparence",
    title: "Apparence",
    icon: Palette,
    body: () => <AppearanceSection />,
  },
];

export function SettingsScreen() {
  // La section ouverte vit dans l'adresse (« #/reglages?section=voix ») : un lien y mène
  // directement, et elle reste ouverte après un rechargement.
  const [params, setParams] = useSearchParams();
  const asked = params.get("section");
  const current =
    asked !== null && SECTIONS.some((section) => section.id === asked) ? asked : FIRST_SECTION;
  const top = useRef<HTMLDivElement>(null);

  const open = (next: string) => {
    setParams({ section: next }, { replace: true });
    // Une section courte ne doit pas s'ouvrir au milieu de la page, sous le défilement de la
    // précédente.
    if ((top.current?.getBoundingClientRect().top ?? 0) < 0) {
      top.current?.scrollIntoView({ block: "start" });
    }
  };

  return (
    <div className={styles.page}>
      <PageHeader
        title="Réglages"
        description="Les clés sont gardées dans le trousseau du système. Une clé enregistrée n'est jamais réaffichée."
      />
      <Tabs.Root
        ref={top}
        className={nav.layout}
        orientation="vertical"
        value={current}
        onValueChange={open}
      >
        <Tabs.List className={nav.list} aria-label="Sections des réglages">
          {SECTIONS.map(({ id, label, icon: Icon }) => (
            <Tabs.Trigger key={id} value={id} className={nav.tab}>
              <Icon className={nav.icon} aria-hidden="true" />
              <span>{label}</span>
            </Tabs.Trigger>
          ))}
        </Tabs.List>
        {SECTIONS.map(({ id, title, body }) => (
          <Tabs.Content key={id} value={id} className={nav.panel}>
            <section className={styles.section} aria-labelledby={id}>
              <h2 id={id} className={styles.sectionTitle}>
                {title}
              </h2>
              {body()}
            </section>
          </Tabs.Content>
        ))}
      </Tabs.Root>
    </div>
  );
}

function AppearanceSection() {
  return (
    <>
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
    </>
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
