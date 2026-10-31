import { describe, expect, it } from "vitest";

import { parseCssColor, readPalette, SphereAnimator, type Palette } from "./animator";
import { FrameRecorder, summarize } from "./perf";
import { fibonacciSphere, rotation } from "./renderer";
import { generateCyberMaskPoints, KIND } from "./cyberMask";
import { reduceMotion, STATE_PARAMS, SPHERE_STATES, STATE_LABELS } from "./states";

const palette: Palette = {
  accent: [0, 1, 1],
  accentStrong: [0.5, 1, 1],
  warning: [1, 0.7, 0.3],
  danger: [1, 0.4, 0.4],
};

function run(animator: SphereAnimator, seconds: number, level = 0) {
  let frame = animator.step(0, level);
  for (let t = 0; t < seconds; t += 1 / 60) frame = animator.step(1 / 60, level);
  return frame;
}

describe("SphereAnimator", () => {
  it("rejoint les paramètres de l'état demandé", () => {
    const animator = new SphereAnimator(palette);
    animator.setState("thinking");
    const frame = run(animator, 2);
    expect(frame.values.rings).toBeCloseTo(STATE_PARAMS.thinking.rings, 2);
    expect(frame.values.spin).toBeCloseTo(STATE_PARAMS.thinking.spin, 2);
  });

  it("fait une transition douce, pas un saut", () => {
    const animator = new SphereAnimator(palette);
    run(animator, 1);
    animator.setState("thinking");
    const first = animator.step(1 / 60, 0);
    expect(first.values.rings).toBeGreaterThan(0);
    expect(first.values.rings).toBeLessThan(STATE_PARAMS.thinking.rings / 2);
  });

  it("prend la couleur d'alerte pour une validation en attente", () => {
    const animator = new SphereAnimator(palette);
    animator.setState("awaiting");
    const frame = run(animator, 2);
    expect(frame.color[0]).toBeCloseTo(1, 2);
    expect(frame.color[1]).toBeCloseTo(0.7, 2);
  });

  it("réagit au niveau audio quand il écoute, pas au repos", () => {
    const listening = new SphereAnimator(palette);
    listening.setState("listening");
    expect(run(listening, 1, 1).audio).toBeGreaterThan(0.3);
    const idle = new SphereAnimator(palette);
    expect(run(idle, 1, 1).audio).toBe(0);
  });

  it("en mouvement réduit, coupe le mouvement mais garde la lumière liée au son", () => {
    const animator = new SphereAnimator(palette);
    animator.setReducedMotion(true);
    animator.setState("speaking");
    const loud = run(animator, 1, 1);
    expect(loud.audio).toBe(0);
    expect(loud.values.spin).toBeLessThan(STATE_PARAMS.speaking.spin * 0.2);
    const quiet = run(animator, 1, 0);
    expect(loud.coreNow).toBeGreaterThan(quiet.coreNow);
  });

  it("ignore les pas de temps aberrants", () => {
    const animator = new SphereAnimator(palette);
    animator.setState("error");
    const frame = animator.step(1000, 5);
    expect(Number.isFinite(frame.values.jitter)).toBe(true);
    expect(frame.values.jitter).toBeLessThan(STATE_PARAMS.error.jitter);
  });
});

describe("états", () => {
  it("chaque état a un libellé et des paramètres", () => {
    for (const state of SPHERE_STATES) {
      expect(STATE_LABELS[state].length).toBeGreaterThan(3);
      expect(STATE_PARAMS[state].pointSize).toBeGreaterThan(0);
    }
  });

  it("le mouvement réduit coupe pulsation, respiration et tremblement", () => {
    const reduced = reduceMotion(STATE_PARAMS.error);
    expect(reduced.jitter).toBe(0);
    expect(reduced.pulseRate).toBe(0);
    expect(reduced.breathe).toBe(0);
    expect(reduced.color).toBe("danger");
  });
});

describe("couleurs", () => {
  it("lit les formes hexadécimales et rgb()", () => {
    expect(parseCssColor("#4fe3ff")).toEqual([79 / 255, 227 / 255, 1]);
    expect(parseCssColor("#fff")).toEqual([1, 1, 1]);
    expect(parseCssColor(" rgb(255 0 0 / 50%) ")).toEqual([1, 0, 0]);
    expect(parseCssColor("rgba(0, 255, 0, 0.3)")).toEqual([0, 1, 0]);
    expect(parseCssColor("rouge")).toBeNull();
  });

  it("prend ses couleurs dans les tokens CSS", () => {
    document.documentElement.style.setProperty("--color-accent", "#000000");
    document.documentElement.style.setProperty("--color-warning", "#ff0000");
    const read = readPalette();
    expect(read.accent).toEqual([0, 0, 0]);
    expect(read.warning).toEqual([1, 0, 0]);
    expect(read.danger).toEqual([0.5, 0.5, 0.5]);
  });
});

describe("géométrie", () => {
  it("place les points sur la sphère unité", () => {
    const { positions, seeds } = fibonacciSphere(500);
    for (let i = 0; i < 500; i += 1) {
      const x = positions[i * 3] ?? 0;
      const y = positions[i * 3 + 1] ?? 0;
      const z = positions[i * 3 + 2] ?? 0;
      expect(Math.hypot(x, y, z)).toBeCloseTo(1, 5);
      expect(seeds[i]).toBeGreaterThanOrEqual(0);
      expect(seeds[i]).toBeLessThan(1);
    }
  });

  it("produit une rotation orthonormée", () => {
    const m = rotation(1.234, 0.4);
    const col = (c: number) => [m[c * 3] ?? 0, m[c * 3 + 1] ?? 0, m[c * 3 + 2] ?? 0];
    for (let a = 0; a < 3; a += 1) {
      expect(Math.hypot(...col(a))).toBeCloseTo(1, 6);
      for (let b = a + 1; b < 3; b += 1) {
        const dot = col(a).reduce((sum, v, i) => sum + v * (col(b)[i] ?? 0), 0);
        expect(dot).toBeCloseTo(0, 6);
      }
    }
  });

  it("génère les points et les identifiants du cyberMask", () => {
    const count = 1000;
    const { positions, seeds, kinds } = generateCyberMaskPoints(count);
    expect(positions.length).toBe(count * 3);
    expect(seeds.length).toBe(count);
    expect(kinds.length).toBe(count);

    let hasFace = false;
    let hasRim = false;
    let hasRain = false;

    for (let i = 0; i < count; i += 1) {
      const x = positions[i * 3] ?? 0;
      const y = positions[i * 3 + 1] ?? 0;
      const z = positions[i * 3 + 2] ?? 0;
      expect(Number.isFinite(x)).toBe(true);
      expect(Number.isFinite(y)).toBe(true);
      expect(Number.isFinite(z)).toBe(true);
      expect(seeds[i]).toBeGreaterThanOrEqual(0);
      expect(seeds[i]).toBeLessThan(1);

      if (kinds[i] === KIND.face) hasFace = true;
      if (kinds[i] === KIND.rim) hasRim = true;
      if (kinds[i] === KIND.rain) hasRain = true;
    }

    expect(hasFace).toBe(true);
    expect(hasRim).toBe(true);
    expect(hasRain).toBe(true);
  });
});

describe("mesure de fluidité", () => {
  it("calcule images par seconde, percentiles et images longues", () => {
    const intervals = [...Array<number>(57).fill(16.7), 33.4, 50, 16.7];
    const sample = summarize("idle", intervals, 1);
    expect(sample.frames).toBe(60);
    expect(sample.fps_mean).toBe(60);
    expect(sample.frame_ms_p50).toBeCloseTo(16.7);
    expect(sample.frame_ms_p99).toBe(50);
    expect(sample.long_frames).toBe(2);
  });

  it("n'enregistre qu'entre start et stop", () => {
    const recorder = new FrameRecorder();
    recorder.record(16);
    recorder.start(0);
    recorder.record(16);
    recorder.record(17);
    const sample = recorder.stop("thinking", 2000);
    recorder.record(16);
    expect(sample.frames).toBe(2);
    expect(sample.duration_s).toBe(2);
    expect(summarize("x", [], 0).fps_mean).toBe(0);
  });
});
