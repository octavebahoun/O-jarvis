import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useEngineStore } from "../lib/engine/store";
import type { EngineEvent } from "../lib/engine/types";
import { Notices } from "./Notices.tsx";

function event(id: number, type: string, payload: Record<string, unknown> = {}): EngineEvent {
  return {
    id,
    type,
    ts: `2026-10-16T08:00:0${String(id)}Z`,
    source: "test",
    correlation_id: null,
    payload,
  };
}

describe("Bulles d'information", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    useEngineStore.setState({ events: [] });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("disparaissent après 8 s, même si d'autres événements arrivent", () => {
    render(<Notices />);
    act(() => {
      useEngineStore
        .getState()
        .pushEvent(event(1, "notification", { title: "Briefing", body: "Bonjour" }));
    });
    expect(screen.getByText("Briefing — Bonjour")).toBeInTheDocument();
    for (let i = 2; i <= 5; i += 1) {
      act(() => {
        vi.advanceTimersByTime(2000);
        useEngineStore.getState().pushEvent(event(i, "voice.state", { state: "idle" }));
      });
    }
    expect(screen.queryByText("Briefing — Bonjour")).not.toBeInTheDocument();
  });
});
