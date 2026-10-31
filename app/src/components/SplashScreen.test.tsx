import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useEngineStore } from "../lib/engine/store";
import { SplashScreen } from "./SplashScreen";

function renderSplash(onComplete: () => void) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <SplashScreen onComplete={onComplete} />
    </QueryClientProvider>,
  );
}

/** Le temps passe par petits pas : React applique ses mises à jour entre deux pas. */
function advance(ms: number) {
  for (let elapsed = 0; elapsed < ms; elapsed += 100) {
    act(() => {
      vi.advanceTimersByTime(100);
    });
  }
}

describe("Écran de démarrage", () => {
  beforeEach(() => {
    // Le temps de l'animation (performance, images) avance avec les minuteries simulées.
    vi.useFakeTimers({
      toFake: [
        "setTimeout",
        "clearTimeout",
        "setInterval",
        "clearInterval",
        "performance",
        "requestAnimationFrame",
        "cancelAnimationFrame",
      ],
    });
    useEngineStore.setState({ phase: "booting", client: null });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("reste affiché tant que le moteur démarre, puis s'efface", () => {
    const onComplete = vi.fn();
    renderSplash(onComplete);
    advance(8000);
    expect(screen.getByText("[ Démarrage du moteur ]")).toBeInTheDocument();
    expect(onComplete).not.toHaveBeenCalled();

    act(() => {
      useEngineStore.setState({ phase: "ready" });
    });
    advance(2000);
    expect(screen.getByText("[ Moteur prêt ]")).toBeInTheDocument();
    expect(onComplete).toHaveBeenCalledTimes(1);
  });

  it("s'efface au plus tard après 15 s, même sans moteur", () => {
    const onComplete = vi.fn();
    renderSplash(onComplete);
    advance(14_000);
    expect(onComplete).not.toHaveBeenCalled();
    advance(2500);
    expect(onComplete).toHaveBeenCalledTimes(1);
  });
});
