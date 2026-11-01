import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import type { SecretStatus, TraceDetail, TraceSummary, VoiceStatus } from "../lib/engine/types";
import { useVoiceStore } from "../lib/voice";
import { fakeEngine, renderScreen } from "../test/render";
import { HomeScreen } from "./home/HomeScreen";
import { JournalScreen } from "./journal/JournalScreen";
import { SettingsScreen } from "./settings/SettingsScreen";

const STATUS = {
  version: "0.1.0",
  dev_mode: true,
  started_at: "2026-10-14T20:00:00Z",
  uptime_s: 3,
  keyring: "Secret Service",
  keyring_error: null,
  secrets_configured: 1,
  secrets_total: 2,
  events_published: 1,
  events_dropped: 0,
  stream_clients: 1,
  timezone: "Europe/Paris",
};

const VOICE: VoiceStatus = {
  state: "idle",
  enabled: true,
  error: null,
  models: { reveil: "prêt", transcription: "prêt", voix_locale: "prêt" },
  echo_cancel: true,
  tts_engine: "cartesia",
  tts_fallback: null,
  stt_engine: "cartesia",
  stt_fallback: false,
  last_latency_ms: 1840,
  last_turn_at: null,
};

/** Réponses communes du faux moteur (modèles, voix), à combiner avec celles d'un test. */
function common(path: string): [number, unknown] | null {
  if (path.startsWith("/v1/plans")) return [200, []];
  if (path === "/v1/suggestions") return [200, []];
  if (path === "/v1/browser") return [200, { sources: [], grants: [], login_domain: null }];
  if (path === "/v1/tools") return [200, []];
  if (path === "/v1/integrations") return [200, []];
  if (path === "/v1/telegram")
    return [
      200,
      { configured: false, connected: false, bot: null, last_error: null, handled: 0, rejected: 0 },
    ];
  if (path === "/v1/llm/status") return [200, []];
  if (path === "/v1/llm/models") return [200, []];
  if (path === "/v1/voice/status") return [200, VOICE];
  if (path === "/v1/local-models") return [200, []];
  if (path === "/v1/voice/devices")
    return [200, { inputs: [{ name: "alsa_input", label: "Micro interne" }], outputs: [] }];
  if (path === "/v1/voice/voices")
    return [200, [{ id: "abc-1", name: "Étienne", gender: null, description: "Posée." }]];
  return null;
}

function secret(over: Partial<SecretStatus>): SecretStatus {
  return {
    name: "DEEPSEEK_API_KEY",
    label: "DeepSeek",
    group: "llm",
    help: "LLM principal.",
    configured: false,
    source: null,
    updated_at: null,
    last_check_ok: null,
    last_check_at: null,
    last_check_detail: null,
    last_check_latency_ms: null,
    ...over,
  };
}

describe("Réglages", () => {
  it("affiche les clés par groupe sans jamais montrer de valeur", async () => {
    const { client } = fakeEngine((_method, path) => {
      const shared = common(path);
      if (shared) return shared;
      if (path === "/v1/secrets")
        return [
          200,
          [
            secret({
              configured: true,
              source: "env",
              last_check_ok: true,
              last_check_detail: "Modèles : deepseek-flash.",
              last_check_latency_ms: 340,
              last_check_at: new Date().toISOString(),
            }),
            secret({ name: "TAVILY_API_KEY", label: "Tavily", group: "search" }),
          ],
        ];
      if (path === "/v1/status") return [200, STATUS];
      if (path === "/v1/settings")
        return [200, { reduced_motion: "system", sphere_quality: "auto" }];
      return [404, {}];
    });
    renderScreen(<SettingsScreen />, { client });
    const llm = await screen.findByRole("region", { name: "Modèles de langage" });
    expect(within(llm).getByText("Configurée, importée du .env")).toBeInTheDocument();
    expect(within(llm).getByText(/Vérifiée — Modèles : deepseek-flash\./)).toBeInTheDocument();
    const search = screen.getByRole("region", { name: "Recherche" });
    expect(within(search).getByText("Non configurée")).toBeInTheDocument();
    expect(within(search).getByRole("button", { name: "Ajouter" })).toBeInTheDocument();
  });

  it("enregistre une clé puis la vérifie, et vide le champ", async () => {
    const user = userEvent.setup();
    let configured = false;
    const { client, calls } = fakeEngine((method, path) => {
      const shared = common(path);
      if (shared) return shared;
      if (path === "/v1/secrets")
        return [
          200,
          [secret({ name: "TAVILY_API_KEY", label: "Tavily", group: "search", configured })],
        ];
      if (method === "PUT") {
        configured = true;
        return [204, null];
      }
      if (path.endsWith("/check"))
        return [
          200,
          { name: "TAVILY_API_KEY", ok: true, detail: "ok", latency_ms: 5, trace_id: "t" },
        ];
      if (path === "/v1/status") return [200, STATUS];
      return [200, { reduced_motion: "system", sphere_quality: "auto" }];
    });
    renderScreen(<SettingsScreen />, { client });
    await user.click(await screen.findByRole("button", { name: "Ajouter" }));
    const input = screen.getByLabelText("Nouvelle valeur pour Tavily");
    expect(input).toHaveAttribute("type", "password");
    await user.type(input, "tvly-valeur-de-test");
    await user.click(screen.getByRole("button", { name: "Enregistrer" }));
    await waitFor(() => {
      expect(calls.some((c) => c.method === "POST" && c.path.endsWith("/check"))).toBe(true);
    });
    const put = calls.find((c) => c.method === "PUT");
    expect(put?.path).toBe("/v1/secrets/TAVILY_API_KEY");
    expect(put?.body).toEqual({ value: "tvly-valeur-de-test" });
    expect(screen.queryByDisplayValue("tvly-valeur-de-test")).not.toBeInTheDocument();
    expect(document.body.textContent).not.toContain("tvly-valeur-de-test");
  });

  it("demande confirmation avant de supprimer une clé", async () => {
    const user = userEvent.setup();
    const { client, calls } = fakeEngine((method, path) => {
      const shared = common(path);
      if (shared) return shared;
      if (path === "/v1/secrets") return [200, [secret({ configured: true, source: "ui" })]];
      if (method === "DELETE") return [204, null];
      if (path === "/v1/status") return [200, STATUS];
      return [200, { reduced_motion: "system", sphere_quality: "auto" }];
    });
    renderScreen(<SettingsScreen />, { client });
    await user.click(await screen.findByRole("button", { name: "Supprimer" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(within(dialog).getByText("Supprimer la clé DeepSeek ?")).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Garder la clé" }));
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);
    await user.click(screen.getByRole("button", { name: "Supprimer" }));
    await user.click(
      within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Supprimer" }),
    );
    await waitFor(() => {
      expect(
        calls.some((c) => c.method === "DELETE" && c.path === "/v1/secrets/DEEPSEEK_API_KEY"),
      ).toBe(true);
    });
  });

  it("dit que le démarrage automatique se règle dans l'app installée", async () => {
    const { client } = fakeEngine((_method, path) => {
      const shared = common(path);
      if (shared) return shared;
      if (path === "/v1/status") return [200, STATUS];
      return [200, []];
    });
    renderScreen(<SettingsScreen />, { client, path: "/?section=demarrage" });
    const section = await screen.findByRole("region", { name: "Démarrage" });
    expect(within(section).getByText(/Réglable dans l'app installée/)).toBeInTheDocument();
  });

  it("change la préférence d'animations", async () => {
    const user = userEvent.setup();
    const { client, calls } = fakeEngine((method, path, body) => {
      const shared = common(path);
      if (shared) return shared;
      if (path === "/v1/settings" && method === "PATCH")
        return [200, { sphere_quality: "auto", ...(body as object) }];
      if (path === "/v1/settings")
        return [200, { reduced_motion: "system", sphere_quality: "auto" }];
      if (path === "/v1/status") return [200, STATUS];
      return [200, []];
    });
    renderScreen(<SettingsScreen />, { client, path: "/?section=apparence" });
    await user.click(await screen.findByRole("radio", { name: /Réduites/ }));
    await waitFor(() => {
      expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ reduced_motion: "reduce" });
    });
  });

  it("montre l'état de la voix et coupe la voix sur demande", async () => {
    const user = userEvent.setup();
    const { client, calls } = fakeEngine((method, path, body) => {
      const shared = common(path);
      if (shared) return shared;
      if (path === "/v1/settings" && method === "PATCH")
        return [200, { voice_enabled: true, voice_engine: "auto", ...(body as object) }];
      if (path === "/v1/settings")
        return [200, { voice_enabled: true, voice_engine: "auto", voice_name: "abc-1" }];
      if (path === "/v1/status") return [200, STATUS];
      return [200, []];
    });
    renderScreen(<SettingsScreen />, { client, path: "/?section=voix" });
    const voice = await screen.findByRole("region", { name: "Voix" });
    expect(await within(voice).findByText("À l'écoute de « Hey Jarvis »")).toBeInTheDocument();
    expect(within(voice).getByText(/Dernière réponse parlée en 1,8 s/)).toBeInTheDocument();
    expect(await within(voice).findByLabelText("Micro")).toHaveDisplayValue(
      "Par défaut du système",
    );
    await user.click(within(voice).getByRole("switch", { name: /Parler à Jarvis/ }));
    await waitFor(() => {
      expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ voice_enabled: false });
    });
  });

  it("propose le profil « Tout Meta » seulement avec une clé Meta", async () => {
    const user = userEvent.setup();
    let metaConfigured = false;
    const provider = (configured: boolean) => ({
      provider: "meta",
      configured,
      local: false,
      available: configured,
      degraded: false,
      last_error: null,
    });
    const { client, calls } = fakeEngine((method, path, body) => {
      if (path === "/v1/llm/status") return [200, [provider(metaConfigured)]];
      const shared = common(path);
      if (shared) return shared;
      if (path === "/v1/settings" && method === "PATCH") return [200, body];
      if (path === "/v1/settings") return [200, { model_profile: "auto", routing: {} }];
      if (path === "/v1/status") return [200, STATUS];
      return [200, []];
    });
    const { unmount } = renderScreen(<SettingsScreen />, { client, path: "/?section=modeles" });
    const models = await screen.findByRole("region", { name: "Modèles" });
    expect(await within(models).findByRole("radio", { name: /Tout Meta/ })).toBeDisabled();
    unmount();
    metaConfigured = true;
    renderScreen(<SettingsScreen />, { client, path: "/?section=modeles" });
    const withKey = await screen.findByRole("region", { name: "Modèles" });
    await waitFor(() => {
      expect(within(withKey).getByRole("radio", { name: /Tout Meta/ })).toBeEnabled();
    });
    await user.click(within(withKey).getByRole("radio", { name: /Tout Meta/ }));
    await waitFor(() => {
      expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ model_profile: "meta" });
    });
  });

  it("règle le relais vers Muse Wonder avec le profil « Tout Meta »", async () => {
    const user = userEvent.setup();
    const { client, calls } = fakeEngine((method, path, body) => {
      const shared = common(path);
      if (shared) return shared;
      if (path === "/v1/settings" && method === "PATCH") return [200, body];
      if (path === "/v1/settings") {
        return [
          200,
          {
            model_profile: "meta",
            relay_after: 2,
            relay_model: "meta/muse-wonder-1.0",
            routing: {},
          },
        ];
      }
      if (path === "/v1/status") return [200, STATUS];
      return [200, []];
    });
    renderScreen(<SettingsScreen />, { client, path: "/?section=modeles" });
    const models = await screen.findByRole("region", { name: "Modèles" });
    const relay = await within(models).findByLabelText(/^Relais/);
    expect(relay).toHaveDisplayValue("Après 2 échecs (recommandé)");
    expect(within(models).getByLabelText(/^Modèle de relais/)).toHaveDisplayValue(
      "meta/muse-wonder-1.0",
    );
    await user.selectOptions(relay, "0");
    await waitFor(() => {
      expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ relay_after: 0 });
    });
  });

  it("n'active le contrôle du PC qu'après confirmation", async () => {
    const user = userEvent.setup();
    let prefs = { os_control_enabled: false, screen_vision: "local" };
    const { client, calls } = fakeEngine((method, path, body) => {
      const shared = common(path);
      if (shared) return shared;
      if (path === "/v1/settings" && method === "PATCH") {
        prefs = { ...prefs, ...(body as object) };
        return [200, prefs];
      }
      if (path === "/v1/settings") return [200, prefs];
      if (path === "/v1/status") return [200, STATUS];
      return [200, []];
    });
    renderScreen(<SettingsScreen />, { client, path: "/?section=controle-pc" });
    const section = await screen.findByRole("region", { name: "Contrôle du PC" });
    expect(await within(section).findByText(/Désactivé : Jarvis ne voit/)).toBeInTheDocument();
    // Tant qu'il est désactivé, le choix de lecture de l'écran est grisé.
    expect(within(section).getByRole("radio", { name: /Sur ce PC/ })).toBeDisabled();
    await user.click(within(section).getByRole("button", { name: "Activer…" }));
    const dialog = await screen.findByRole("alertdialog", { name: /Activer le contrôle du PC/ });
    await user.click(within(dialog).getByRole("button", { name: "Annuler" }));
    expect(calls.some((c) => c.method === "PATCH")).toBe(false);
    await user.click(within(section).getByRole("button", { name: "Activer…" }));
    await user.click(await screen.findByRole("button", { name: "Activer" }));
    await waitFor(() => {
      expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ os_control_enabled: true });
    });
    expect(await within(section).findByText(/Activé : Jarvis peut/)).toBeInTheDocument();
    await user.click(within(section).getByRole("radio", { name: /En ligne/ }));
    await waitFor(() => {
      expect(calls.filter((c) => c.method === "PATCH").at(-1)?.body).toEqual({
        screen_vision: "en_ligne",
      });
    });
  });

  it("montre l'état hors ligne", () => {
    renderScreen(<SettingsScreen />, { client: null, stream: "closed" });
    expect(screen.getByText("Moteur hors ligne")).toBeInTheDocument();
  });

  it("montre une erreur du moteur avec un bouton pour réessayer", async () => {
    const { client } = fakeEngine(() => [
      500,
      {
        error: {
          code: "internal_error",
          message: "Erreur interne du moteur.",
          details: {},
          correlation_id: null,
        },
      },
    ]);
    renderScreen(<SettingsScreen />, { client });
    // Chaque section dit son erreur et propose de réessayer.
    expect((await screen.findAllByText("Erreur interne du moteur.")).length).toBeGreaterThan(0);
    expect(screen.getAllByRole("button", { name: "Réessayer" }).length).toBeGreaterThan(0);
  });
});

describe("Journal", () => {
  const trace: TraceSummary = {
    id: "t1",
    name: "Vérification DeepSeek",
    kind: "check",
    status: "ok",
    started_at: "2026-10-14T20:00:00Z",
    duration_ms: 365,
    correlation_id: "abc",
    error: null,
    span_count: 1,
    meta: {},
  };
  const detail: TraceDetail = {
    ...trace,
    ended_at: "2026-10-14T20:00:00.365Z",
    spans: [
      {
        id: "s1",
        parent_id: null,
        name: "appel fournisseur",
        kind: "http",
        status: "ok",
        started_at: "2026-10-14T20:00:00.010Z",
        ended_at: "2026-10-14T20:00:00.350Z",
        duration_ms: 340,
        attributes: { secret: "DEEPSEEK_API_KEY", ok: true },
        error: null,
      },
    ],
  };

  it("liste les traces puis montre les étapes de celle choisie", async () => {
    const user = userEvent.setup();
    const { client } = fakeEngine((_, path) =>
      path.startsWith("/v1/traces/") ? [200, detail] : [200, [trace]],
    );
    renderScreen(<JournalScreen />, { client });
    await user.click(await screen.findByRole("button", { name: /Vérification DeepSeek/ }));
    expect(
      await screen.findByRole("heading", { name: "Vérification DeepSeek" }),
    ).toBeInTheDocument();
    expect(screen.getByText("appel fournisseur")).toBeInTheDocument();
    expect(screen.getByText("DEEPSEEK_API_KEY")).toBeInTheDocument();
    expect(screen.getAllByText("340 ms").length).toBeGreaterThan(0);
  });

  it("explique comment créer une première trace", async () => {
    const { client } = fakeEngine(() => [200, []]);
    renderScreen(<JournalScreen />, { client });
    expect(await screen.findByText("Aucune trace pour l'instant")).toBeInTheDocument();
  });
});

describe("Accueil", () => {
  afterEach(() => {
    useVoiceStore.setState({ phase: "off", transcript: null, conversationId: null });
  });

  it("prévisualise les états de la sphère et les annonce", async () => {
    const user = userEvent.setup();
    const { client } = fakeEngine((_, path) =>
      path === "/v1/status"
        ? [200, STATUS]
        : path === "/v1/suggestions"
          ? [200, []]
          : [200, { reduced_motion: "system", sphere_quality: "auto" }],
    );
    renderScreen(<HomeScreen />, { client });
    expect(
      await screen.findByRole("img", { name: "Sphère de Jarvis : En veille" }),
    ).toBeInTheDocument();
    await user.click(await screen.findByRole("button", { name: "Je réfléchis" }));
    expect(
      screen.getByRole("img", { name: "Sphère de Jarvis : Je réfléchis" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Je réfléchis" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByText("Jarvis est prêt.")).toBeInTheDocument();
  });

  it("invite à dire « Hey Jarvis » et pilote la voix", async () => {
    const user = userEvent.setup();
    const { client, calls } = fakeEngine((_method, path) => {
      const shared = common(path);
      if (shared) return shared;
      if (path === "/v1/status") return [200, STATUS];
      if (path === "/v1/voice/listen" || path === "/v1/voice/stop") return [204, undefined];
      return [200, { reduced_motion: "system", sphere_quality: "auto" }];
    });
    renderScreen(<HomeScreen />, { client });
    expect(await screen.findByText("Dis « Hey Jarvis », ou écris-moi.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Parler à Jarvis" }));
    await waitFor(() => {
      expect(calls.some((c) => c.method === "POST" && c.path === "/v1/voice/listen")).toBe(true);
    });

    act(() => {
      useVoiceStore.getState().setPhase("speaking");
      useVoiceStore.getState().setTranscript("quelle heure est-il ?");
    });
    expect(screen.getByRole("img", { name: "Sphère de Jarvis : Je parle" })).toBeInTheDocument();
    expect(screen.getByText("Tu as dit : « quelle heure est-il ? »")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Arrêter la voix" }));
    await waitFor(() => {
      expect(calls.some((c) => c.method === "POST" && c.path === "/v1/voice/stop")).toBe(true);
    });
  });

  it("passe la sphère en erreur quand le moteur est arrêté", () => {
    renderScreen(<HomeScreen />, { client: null, phase: "failed", stream: "closed" });
    expect(
      screen.getByRole("img", { name: "Sphère de Jarvis : Un problème est survenu" }),
    ).toBeInTheDocument();
  });
});
