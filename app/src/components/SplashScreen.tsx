import { useEffect, useRef, useState } from "react";

import { useEngineStore } from "../lib/engine/store";
import styles from "./SplashScreen.module.css";

interface Particle {
  x: number;
  y: number;
  tx: number;
  ty: number;
  vx: number;
  vy: number;
  size: number;
  color: string;
  alpha: number;
}

/** Les particules rejoignent les lettres pendant ce temps. */
const FORM_MS = 2475;
/** Le logo reste au moins jusque-là, même si le moteur est déjà prêt. */
const MIN_MS = 3960;
/** Au-delà, l'écran s'efface même sans moteur : l'app montre alors son propre état. */
const MAX_WAIT_MS = 15_000;
const DISPERSE_MS = 540;
const FADE_MS = 800;

const STATUS = {
  booting: "Démarrage du moteur",
  ready: "Moteur prêt",
  failed: "Moteur indisponible",
} as const;

/** Une particule par point du texte O-JARVIS, partie d'un endroit au hasard. */
function textParticles(width: number, height: number): Particle[] {
  const textCanvas = document.createElement("canvas");
  textCanvas.width = width;
  textCanvas.height = height;
  const tCtx = textCanvas.getContext("2d");
  if (!tCtx) return [];
  const fontSize = Math.min(width * 0.13, 110);
  tCtx.font = `900 ${fontSize}px "Space Grotesk", sans-serif`;
  tCtx.fillStyle = "#ffffff";
  tCtx.textAlign = "center";
  tCtx.textBaseline = "middle";
  tCtx.fillText("O-JARVIS", width / 2, height / 2 - 20);
  const imgData = tCtx.getImageData(0, 0, width, height);
  const step = Math.max(3, Math.floor(width / 320)); // densité de particules
  const targets: { x: number; y: number }[] = [];
  for (let y = 0; y < height; y += step) {
    for (let x = 0; x < width; x += step) {
      const idx = (y * width + x) * 4;
      if ((imgData.data[idx + 3] ?? 0) > 128) targets.push({ x, y });
    }
  }
  const colors = ["#00f0ff", "#00ff88", "#38bdf8", "#ffffff"];
  return targets.map((t) => {
    const angle = Math.random() * Math.PI * 2;
    const dist = Math.random() * Math.max(width, height) * 0.7 + 100;
    return {
      x: width / 2 + Math.cos(angle) * dist,
      y: height / 2 + Math.sin(angle) * dist,
      tx: t.x,
      ty: t.y,
      vx: 0,
      vy: 0,
      size: Math.random() * 1.8 + 1,
      color: colors[Math.floor(Math.random() * colors.length)] ?? "#00f0ff",
      alpha: Math.random() * 0.7 + 0.3,
    };
  });
}

function drawParticles(
  ctx: CanvasRenderingContext2D,
  particles: Particle[],
  size: { width: number; height: number },
  elapsed: number,
  dispersing: boolean,
) {
  const { width, height } = size;
  ctx.fillStyle = "rgba(8, 12, 16, 0.22)";
  ctx.fillRect(0, 0, width, height);
  // Une fois formées, les lettres respirent en attendant le moteur.
  const breath = elapsed > FORM_MS && !dispersing ? Math.sin((elapsed - FORM_MS) * 0.005) * 0.5 : 0;
  // Force d'attraction douce au début, plus franche ensuite.
  const ease = Math.min(0.045, 0.008 + Math.min(1, elapsed / 4500) * 0.04);
  for (const pt of particles) {
    if (!dispersing) {
      pt.vx = (pt.vx + (pt.tx - pt.x) * ease) * 0.86;
      pt.vy = (pt.vy + (pt.ty - pt.y) * ease) * 0.86;
      pt.x += pt.vx;
      pt.y += pt.vy;
    } else {
      // Éclatement vers l'extérieur avant de laisser place à l'app.
      const angle = Math.atan2(pt.y - height / 2, pt.x - width / 2);
      pt.x += Math.cos(angle) * (pt.size * 3.5);
      pt.y += Math.sin(angle) * (pt.size * 3.5);
      pt.alpha *= 0.94;
    }
    ctx.fillStyle = pt.color;
    ctx.globalAlpha = Math.max(0, pt.alpha);
    ctx.beginPath();
    ctx.arc(pt.x, pt.y, Math.max(0.5, pt.size + breath), 0, Math.PI * 2);
    ctx.fill();
  }
  ctx.globalAlpha = 1;
}

/**
 * Écran de démarrage : le logo se forme pendant que le moteur démarre en arrière-plan.
 * Il s'efface quand le moteur a répondu (prêt ou en échec) et que l'animation est finie,
 * au plus tard après MAX_WAIT_MS.
 */
export function SplashScreen({
  onComplete,
  onLeave,
}: {
  onComplete?: () => void;
  onLeave?: () => void;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const phase = useEngineStore((s) => s.phase);
  const phaseRef = useRef(phase);
  const onCompleteRef = useRef(onComplete);
  const onLeaveRef = useRef(onLeave);
  const [animationDone, setAnimationDone] = useState(false);
  const [capped, setCapped] = useState(false);
  const [progress, setProgress] = useState(0);
  const closing = capped || animationDone;

  useEffect(() => {
    phaseRef.current = phase;
  }, [phase]);

  useEffect(() => {
    onCompleteRef.current = onComplete;
  }, [onComplete]);

  useEffect(() => {
    onLeaveRef.current = onLeave;
  }, [onLeave]);

  // La barre avance avec le temps jusqu'à 90 %, la fin attend le moteur.
  useEffect(() => {
    const start = performance.now();
    const timer = window.setInterval(() => {
      const elapsed = performance.now() - start;
      const ready = phaseRef.current === "ready";
      setProgress(ready ? 100 : Math.round(Math.min(elapsed / MIN_MS, 1) * 90));
      if (elapsed >= MAX_WAIT_MS) setCapped(true);
    }, 200);
    return () => {
      window.clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    if (!closing) return;
    onLeaveRef.current?.();
    const timer = window.setTimeout(() => onCompleteRef.current?.(), FADE_MS);
    return () => {
      window.clearTimeout(timer);
    };
  }, [closing]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    const size = { width: window.innerWidth, height: window.innerHeight };
    canvas.width = size.width;
    canvas.height = size.height;
    const onResize = () => {
      size.width = canvas.width = window.innerWidth;
      size.height = canvas.height = window.innerHeight;
    };
    window.addEventListener("resize", onResize);
    const particles = ctx ? textParticles(size.width, size.height) : [];
    const start = performance.now();
    let leaveAt: number | null = null;
    let frame = 0;

    const render = (now: number) => {
      const elapsed = now - start;
      const engineAnswered = phaseRef.current !== "booting";
      if (leaveAt === null && elapsed >= MIN_MS && (engineAnswered || elapsed >= MAX_WAIT_MS)) {
        leaveAt = elapsed;
      }
      if (leaveAt !== null && elapsed - leaveAt >= DISPERSE_MS) {
        setAnimationDone(true);
        return;
      }
      if (ctx) drawParticles(ctx, particles, size, elapsed, leaveAt !== null);
      frame = requestAnimationFrame(render);
    };
    frame = requestAnimationFrame(render);

    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener("resize", onResize);
    };
  }, []);

  return (
    <div
      className={styles.splash}
      data-closing={closing}
      role="status"
      aria-label="Chargement d'O-jarvis"
    >
      <canvas ref={canvasRef} className={styles.canvas} aria-hidden="true" />
      <div className={styles.meta}>
        <span className={styles.status}>[ {STATUS[phase]} ]</span>
        <div className={styles.progressBar}>
          <div className={styles.progressFill} style={{ width: `${progress}%` }} />
        </div>
      </div>
    </div>
  );
}
