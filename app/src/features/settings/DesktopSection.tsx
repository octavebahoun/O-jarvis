import { MonitorCog, MonitorUp } from "lucide-react";

import { Button } from "../../components/Button";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { describeError } from "../../lib/engine/client";
import {
  usePreferences,
  useScreenShare,
  useToggleScreenShare,
  useUpdatePreferences,
} from "../../lib/engine/queries";
import { useEngineOnline } from "../../lib/engine/store";
import { Choice } from "./PreferenceControls";
import styles from "./SettingsScreen.module.css";

/** Partager l'écran en direct : les outils d'écran lisent le flux au lieu d'une capture. */
function ScreenShareRow({ enabled, online }: { enabled: boolean; online: boolean }) {
  const share = useScreenShare();
  const toggle = useToggleScreenShare();
  const active = share.data?.active ?? false;
  return (
    <li className={styles.secret}>
      <div className={styles.grantHead}>
        <MonitorUp className={styles.secretIcon} aria-hidden="true" />
        <div className={styles.secretText}>
          <p className={styles.secretLabel}>Partager mon écran en direct</p>
          <p className={styles.secretHelp}>
            Jarvis voit ton écran en continu (deux images par seconde, gardées en mémoire, jamais
            enregistrées) au lieu d&apos;une capture à chaque fois. GNOME te demande ton accord la
            première fois et affiche son indicateur tant que dure le partage.
          </p>
          <p className={styles.integrationStatus} data-tone={active ? "ok" : "idle"}>
            {active ? "Écran partagé." : "Écran non partagé."}
          </p>
        </div>
        <div className={styles.actions}>
          <Button
            size="sm"
            busy={toggle.isPending}
            disabled={!online || (!enabled && !active)}
            onClick={() => {
              toggle.mutate(!active);
            }}
          >
            {active ? "Arrêter" : "Partager…"}
          </Button>
        </div>
      </div>
      {toggle.isError && (
        <p className={styles.ko} role="alert">
          {describeError(toggle.error)}
        </p>
      )}
    </li>
  );
}

/** Contrôle du PC : désactivé par défaut ; l'activer demande une confirmation. */
export function DesktopSection() {
  const preferences = usePreferences();
  const update = useUpdatePreferences();
  const online = useEngineOnline();
  if (!online && !preferences.data)
    return <p className={styles.note}>Disponible dès que le moteur répondra.</p>;
  const enabled = preferences.data?.os_control_enabled ?? false;
  const disabled = !online || !preferences.data || update.isPending;
  return (
    <>
      <ul className={styles.secrets}>
        <li className={styles.secret}>
          <div className={styles.grantHead}>
            <MonitorCog className={styles.secretIcon} aria-hidden="true" />
            <div className={styles.secretText}>
              <p className={styles.secretLabel}>Applications, fichiers et écran</p>
              <p className={styles.secretHelp}>
                Ouvrir et fermer tes applications, déplacer, copier et créer des fichiers dans ton
                dossier personnel, regarder ton écran. Chaque action attend ta validation ; seule la
                lecture de l&apos;écran se fait sans demander.
              </p>
              <p className={styles.integrationStatus} data-tone={enabled ? "ok" : "idle"}>
                {enabled
                  ? "Activé : Jarvis peut te proposer ces actions."
                  : "Désactivé : Jarvis ne voit ni tes applications, ni tes fichiers, ni ton écran."}
              </p>
            </div>
            <div className={styles.actions}>
              {enabled ? (
                <Button
                  size="sm"
                  disabled={disabled}
                  onClick={() => {
                    update.mutate({ os_control_enabled: false });
                  }}
                >
                  Désactiver
                </Button>
              ) : (
                <ConfirmDialog
                  trigger={
                    <Button size="sm" disabled={disabled}>
                      Activer…
                    </Button>
                  }
                  title="Activer le contrôle du PC ?"
                  description="Jarvis pourra te proposer d'ouvrir ou de fermer une application, de ranger tes fichiers et de regarder ton écran. Rien ne se fait sans ta validation, sauf regarder l'écran. Tu peux le désactiver à tout moment."
                  confirmLabel="Activer"
                  onConfirm={() => {
                    update.mutate({ os_control_enabled: true });
                  }}
                />
              )}
            </div>
          </div>
          {update.isError && (
            <p className={styles.ko} role="alert">
              {describeError(update.error)}
            </p>
          )}
        </li>
        <ScreenShareRow enabled={enabled} online={online} />
      </ul>
      {preferences.data?.model_profile === "meta" ? (
        // « Tout Meta » n'a pas de modèle local de vision : Muse Spark lit toujours l'écran.
        <p className={styles.note}>
          Lecture de l'écran : en ligne, par Muse Spark (profil « Tout Meta »). La capture part chez
          Meta seulement quand tu demandes à Jarvis de regarder ou de cliquer.
        </p>
      ) : (
        <ScreenVisionChoice enabled={enabled} />
      )}
    </>
  );
}

/** « Automatique » : lire l'écran sur ce PC (lent) ou en ligne (rapide). */
function ScreenVisionChoice({ enabled }: { enabled: boolean }) {
  return (
    <Choice
      name="screen_vision"
      label="Lecture de l'écran"
      disabled={!enabled}
      options={[
        {
          value: "local",
          label: "Sur ce PC",
          hint: "Rien ne sort de ta machine. Lent : 30 s à 2 min selon l'écran.",
        },
        {
          value: "en_ligne",
          label: "En ligne",
          hint: "Rapide. La capture part chez OpenRouter ; s'il est saturé, lecture sur ce PC.",
        },
      ]}
    />
  );
}
