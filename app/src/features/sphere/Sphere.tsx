import { useEffect, useRef, useState } from "react";

import { uiLog } from "../../lib/log";
import { readPalette, SphereAnimator } from "./animator";
import { type AvatarModel, SphereRenderer } from "./renderer";
import styles from "./Sphere.module.css";
import { STATE_LABELS, type SphereState } from "./states";

export type SphereQuality = "auto" | "high" | "low";

export interface SphereProps {
  state: SphereState;
  /** Modèle visuel de l'avatar : sphère classique ou masque cybernétique. */
  avatarModel?: AvatarModel;
  /** Niveau audio courant (0 à 1), lu à chaque image sans rendu React. */
  level?: () => number;
  quality?: SphereQuality;
  reducedMotion?: boolean;
  /** Appelé à chaque image avec l'intervalle en millisecondes (mesure de fluidité). */
  onFrame?: (intervalMs: number) => void;
}

const SPHERE_POINTS: Record<SphereQuality, number> = { auto: 3400, high: 5200, low: 1600 };
const MASK_POINTS: Record<SphereQuality, number> = { auto: 8000, high: 14000, low: 3500 };
const MAX_DPR: Record<SphereQuality, number> = { auto: 2, high: 2, low: 1 };

export function Sphere({
  state,
  avatarModel = "sphere",
  level,
  quality = "auto",
  reducedMotion = false,
  onFrame,
}: SphereProps) {
  const wrapperRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const animatorRef = useRef<SphereAnimator | null>(null);
  const levelRef = useRef(level);
  const frameRef = useRef(onFrame);
  const [webgl, setWebgl] = useState(true);

  useEffect(() => {
    levelRef.current = level;
    frameRef.current = onFrame;
  });

  useEffect(() => {
    animatorRef.current?.setState(state);
  }, [state]);

  useEffect(() => {
    animatorRef.current?.setReducedMotion(reducedMotion);
  }, [reducedMotion]);

  useEffect(() => {
    const canvas = canvasRef.current;
    const wrapper = wrapperRef.current;
    if (!canvas || !wrapper) return;

    const pointCount = avatarModel === "cyberMask" ? MASK_POINTS[quality] : SPHERE_POINTS[quality];
    const renderer = SphereRenderer.create(canvas, {
      pointCount,
      avatarModel,
    });
    if (!renderer) {
      uiLog("warn", "sphère : WebGL2 indisponible, repli CSS");
      setWebgl(false);
      return;
    }
    uiLog("info", `sphère : WebGL2 prêt (${pointCount} points, modèle ${avatarModel})`);
    setWebgl(true);
    const animator = animatorRef.current ?? new SphereAnimator(readPalette());
    animator.setState(state);
    animator.setReducedMotion(reducedMotion);
    animatorRef.current = animator;

    const resize = () => {
      const rect = wrapper.getBoundingClientRect();
      const dpr = Math.min(window.devicePixelRatio || 1, MAX_DPR[quality]);
      renderer.resize(rect.width, rect.height, dpr);
    };
    resize();
    const observer = new ResizeObserver(resize);
    observer.observe(wrapper);

    let frame = 0;
    let last = performance.now();
    const loop = (now: number) => {
      const dtMs = now - last;
      last = now;
      const params = animator.step(dtMs / 1000, levelRef.current?.() ?? 0);
      renderer.render(params, dtMs / 1000);
      frameRef.current?.(dtMs);
      frame = requestAnimationFrame(loop);
    };
    const start = () => {
      cancelAnimationFrame(frame);
      last = performance.now();
      frame = requestAnimationFrame(loop);
    };
    const onVisibility = () => {
      if (document.hidden) cancelAnimationFrame(frame);
      else start();
    };
    const onLost = (event: Event) => {
      uiLog("warn", "sphère : contexte WebGL perdu");
      event.preventDefault();
      cancelAnimationFrame(frame);
    };
    document.addEventListener("visibilitychange", onVisibility);
    canvas.addEventListener("webglcontextlost", onLost);
    start();

    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
      document.removeEventListener("visibilitychange", onVisibility);
      canvas.removeEventListener("webglcontextlost", onLost);
      renderer.dispose();
    };
    // L'état et le mouvement réduit passent par l'animateur, sans recréer le rendu.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [quality, avatarModel]);

  return (
    <div
      ref={wrapperRef}
      className={styles.wrapper}
      role="img"
      aria-label={`Sphère de Jarvis : ${STATE_LABELS[state]}`}
      data-state={state}
      data-model={avatarModel}
    >
      <canvas ref={canvasRef} className={styles.canvas} aria-hidden="true" />
      {!webgl && <div className={styles.fallback} data-state={state} />}
    </div>
  );
}
