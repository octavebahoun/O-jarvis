import {
  Bot,
  BrainCircuit,
  CalendarClock,
  History,
  ListChecks,
  Orbit,
  Puzzle,
  ScrollText,
  Settings2,
} from "lucide-react";
import { useEffect, type ComponentType, type SVGProps } from "react";
import { NavLink, Outlet } from "react-router";

import { quitApp } from "../lib/engine/connection";
import { usePlans } from "../lib/engine/queries";
import { useEngineStore } from "../lib/engine/store";
import { useReducedMotion } from "../lib/motion";
import { Notices } from "./Notices.tsx";
import styles from "./Shell.module.css";

interface NavItem {
  to: string;
  label: string;
  icon: ComponentType<SVGProps<SVGSVGElement>>;
}

const NAV: NavItem[] = [
  { to: "/", label: "Accueil", icon: Orbit },
  { to: "/historique", label: "Historique", icon: History },
  { to: "/memoire", label: "Mémoire", icon: BrainCircuit },
  { to: "/actions", label: "Actions à valider", icon: ListChecks },
  { to: "/automatisations", label: "Automatisations", icon: CalendarClock },
  { to: "/agents", label: "Agents", icon: Bot },
  { to: "/skills", label: "Skills et plugins", icon: Puzzle },
  { to: "/journal", label: "Journal et traces", icon: ScrollText },
  { to: "/reglages", label: "Réglages", icon: Settings2 },
];

function EngineIndicator() {
  const phase = useEngineStore((s) => s.phase);
  const stream = useEngineStore((s) => s.stream);
  let tone: "ok" | "wait" | "down" = "wait";
  let label = "Démarrage du moteur…";
  if (phase === "failed") {
    tone = "down";
    label = "Moteur arrêté";
  } else if (phase === "ready" && stream === "open") {
    tone = "ok";
    label = "Moteur connecté";
  } else if (phase === "ready") {
    label = "Reconnexion au moteur…";
  }
  return (
    <p className={styles.engine} data-tone={tone} role="status" aria-live="polite">
      <span className={styles.dot} aria-hidden="true" />
      <span className={styles.engineLabel}>{label}</span>
    </p>
  );
}

function OfflineBanner() {
  const phase = useEngineStore((s) => s.phase);
  const stream = useEngineStore((s) => s.stream);
  const error = useEngineStore((s) => s.error);
  if (phase === "booting" || (phase === "ready" && stream !== "closed")) return null;
  const message =
    phase === "failed"
      ? `Le moteur n'a pas démarré${error ? ` : ${error}` : "."}`
      : "Le moteur ne répond plus. Reconnexion automatique en cours…";
  return (
    <div className={styles.banner} role="alert">
      {message}
    </div>
  );
}

function useQuitShortcut() {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.ctrlKey && !event.shiftKey && !event.altKey && event.key.toLowerCase() === "q") {
        event.preventDefault();
        void quitApp();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
    };
  }, []);
}

/** Nombre de plans qui attendent ta décision (affiché à côté de « Actions à valider »). */
function PendingBadge() {
  const pending = usePlans("pending");
  const count = pending.data?.length ?? 0;
  if (count === 0) return null;
  return (
    <span className={styles.badge}>
      {count}
      <span className="visually-hidden"> en attente</span>
    </span>
  );
}

export function Shell() {
  useReducedMotion();
  useQuitShortcut();
  return (
    <div className={styles.shell}>
      <a className="skip-link" href="#contenu">
        Aller au contenu
      </a>
      <nav className={styles.sidebar} aria-label="Navigation principale">
        <div className={styles.brand}>
          <span className={styles.logo} aria-hidden="true" />
          <span className={styles.brandName}>O-jarvis</span>
        </div>
        <ul className={styles.nav}>
          {NAV.map(({ to, label, icon: Icon }) => (
            <li key={to}>
              <NavLink
                to={to}
                end={to === "/"}
                className={({ isActive }) =>
                  isActive ? `${styles.link} ${styles.active}` : styles.link
                }
                title={label}
              >
                <Icon className={styles.linkIcon} aria-hidden="true" />
                <span className={styles.linkLabel}>{label}</span>
                {to === "/actions" && <PendingBadge />}
              </NavLink>
            </li>
          ))}
        </ul>
        <EngineIndicator />
      </nav>
      <div className={styles.main}>
        <OfflineBanner />
        <main id="contenu" className={styles.content} tabIndex={-1}>
          <Outlet />
        </main>
        <Notices />
      </div>
    </div>
  );
}
