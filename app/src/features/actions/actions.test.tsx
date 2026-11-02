import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import type { AuditPage, BrowserState, Plan } from "../../lib/engine/types";
import { fakeEngine, renderScreen } from "../../test/render";
import type { ChatItem } from "../chat/useChat";
import { ChatMessage } from "../chat/ChatMessage";
import { JournalScreen } from "../journal/JournalScreen";
import { SettingsScreen } from "../settings/SettingsScreen";
import { ActionsScreen } from "./ActionsScreen";

function plan(over: Partial<Plan> = {}): Plan {
  return {
    id: "p1",
    conversation_id: "c1",
    title: "Chercher le thé vert sur la boutique",
    request: "Trouve le prix du thé vert",
    status: "proposed",
    max_level: 2,
    tainted: false,
    created_at: "2026-10-15T12:00:00Z",
    updated_at: "2026-10-15T12:00:00Z",
    decided_at: null,
    result: null,
    steps: [
      {
        position: 1,
        tool: "navigateur_remplir",
        arguments: { champ: "Recherche", valeur: "thé vert" },
        why: "Taper la recherche.",
        level: 2,
        description: "Écrire « thé vert » dans le champ « Recherche »",
        status: "pending",
        result: null,
        error: null,
      },
      {
        position: 2,
        tool: "navigateur_cliquer",
        arguments: { element: "Chercher" },
        why: "Lancer la recherche.",
        level: 2,
        description: "Cliquer sur « Chercher » dans la page ouverte",
        status: "pending",
        result: null,
        error: null,
      },
    ],
    ...over,
  };
}

function engine(plans: Plan[], extra?: (method: string, path: string) => [number, unknown] | null) {
  return fakeEngine((method, path, body) => {
    const custom = extra?.(method, path);
    if (custom) return custom;
    if (path.startsWith("/v1/plans?")) return [200, plans];
    if (method === "POST" && path.endsWith("/approve")) {
      const doubled = (body as { double_confirm: boolean }).double_confirm;
      return [200, { ...plans[0], status: "approved", decided_at: doubled ? "x" : "y" }];
    }
    if (method === "POST" && path.endsWith("/refuse"))
      return [200, { ...plans[0], status: "refused" }];
    if (path.startsWith("/v1/plans/")) return [200, plans[0]];
    return [200, []];
  });
}

describe("Actions à valider", () => {
  it("dit quand rien n'attend de décision", async () => {
    const { client } = engine([]);
    renderScreen(<ActionsScreen />, { client });
    expect(await screen.findByText("Rien n'attend ta décision")).toBeInTheDocument();
  });

  it("montre ce qui va se passer, sur quoi et à quel niveau ; refuser est aussi simple", async () => {
    const { client, calls } = engine([plan({ tainted: true })]);
    const user = userEvent.setup();
    renderScreen(<ActionsScreen />, { client });
    const card = await screen.findByRole("article", {
      name: "Chercher le thé vert sur la boutique",
    });
    expect(
      within(card).getByText("Écrire « thé vert » dans le champ « Recherche »"),
    ).toBeInTheDocument();
    expect(within(card).getAllByText("Niveau 2 · Action").length).toBe(3);
    expect(within(card).getByRole("note")).toHaveTextContent("contenu externe");
    const refuse = within(card).getByRole("button", { name: "Refuser" });
    const approve = within(card).getByRole("button", { name: "Valider" });
    expect(refuse).toBeEnabled();
    expect(approve).toBeEnabled();
    const [firstDetails] = within(card).getAllByRole("button", { name: "Détails" });
    if (firstDetails) await user.click(firstDetails);
    expect(within(card).getByText("navigateur_remplir")).toBeInTheDocument();
    await user.click(refuse);
    await waitFor(() => {
      expect(calls.some((c) => c.method === "POST" && c.path === "/v1/plans/p1/refuse")).toBe(true);
    });
  });

  it("valide un plan d'action en un clic", async () => {
    const { client, calls } = engine([plan()]);
    const user = userEvent.setup();
    renderScreen(<ActionsScreen />, { client });
    await user.click(await screen.findByRole("button", { name: "Valider" }));
    await waitFor(() => {
      const call = calls.find((c) => c.path === "/v1/plans/p1/approve");
      expect(call?.body).toEqual({ double_confirm: false });
    });
  });

  it("demande une seconde confirmation pour une destruction", async () => {
    const destructive = plan({
      title: "Supprimer le brouillon",
      max_level: 3,
      steps: [
        {
          position: 1,
          tool: "supprimer_fichier",
          arguments: { chemin: "~/brouillon.txt" },
          why: "Tu l'as demandé.",
          level: 3,
          description: "Mettre à la corbeille le fichier ~/brouillon.txt",
          status: "pending",
          result: null,
          error: null,
        },
      ],
    });
    const { client, calls } = engine([destructive]);
    const user = userEvent.setup();
    renderScreen(<ActionsScreen />, { client });
    await user.click(await screen.findByRole("button", { name: "Valider…" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toHaveTextContent("mettre à la corbeille le fichier ~/brouillon.txt");
    await user.click(within(dialog).getByRole("button", { name: "Annuler" }));
    expect(calls.some((c) => c.path.endsWith("/approve"))).toBe(false);
    await user.click(screen.getByRole("button", { name: "Valider…" }));
    await user.click(
      within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Oui, exécuter" }),
    );
    await waitFor(() => {
      const call = calls.find((c) => c.path === "/v1/plans/p1/approve");
      expect(call?.body).toEqual({ double_confirm: true });
    });
  });

  it("affiche le plan proposé dans la conversation", async () => {
    const { client } = engine([plan()]);
    const item: ChatItem = {
      key: "a1",
      id: 2,
      role: "assistant",
      content: "J'ai préparé un plan, il attend ta validation.",
      status: "ok",
      meta: null,
      context: null,
      error: null,
      tools: [
        { id: "t1", name: "navigateur_ouvrir", description: "Ouvrir la boutique", status: "done" },
      ],
      plans: ["p1"],
    };
    renderScreen(<ul>{<ChatMessage item={item} />}</ul>, { client });
    expect(screen.getByRole("list", { name: "Outils utilisés" })).toHaveTextContent(
      "Ouvrir la boutique",
    );
    expect(await screen.findByRole("button", { name: "Valider" })).toBeInTheDocument();
  });
});

describe("Journal des actions", () => {
  it("liste les actions et vérifie la chaîne d'empreintes", async () => {
    const page: AuditPage = {
      items: [
        {
          id: 2,
          ts: "2026-10-15T12:01:00Z",
          actor: "jarvis",
          action: "outil.navigateur_cliquer",
          level: 2,
          summary: "Cliquer sur « Chercher » dans la page ouverte",
          details: { plan: "p1", etape: 2 },
          hash: "ab".repeat(32),
        },
        {
          id: 1,
          ts: "2026-10-15T12:00:00Z",
          actor: "utilisateur",
          action: "plan.approuve",
          level: 2,
          summary: "Plan validé : Chercher le thé vert",
          details: {},
          hash: "cd".repeat(32),
        },
      ],
      total: 2,
      integrity_ok: true,
      broken_at: null,
    };
    const { client, calls } = fakeEngine((_method, path) => {
      if (path.startsWith("/v1/audit/verify"))
        return [200, { ok: true, count: 2, broken_at: null }];
      if (path.startsWith("/v1/audit")) return [200, page];
      return [200, []];
    });
    const user = userEvent.setup();
    renderScreen(<JournalScreen />, { client });
    await user.click(screen.getByRole("tab", { name: "Actions" }));
    expect(await screen.findByText("2 entrées, chaîne d'empreintes intacte.")).toBeInTheDocument();
    expect(screen.getByText("Plan validé : Chercher le thé vert")).toBeInTheDocument();
    expect(screen.getByText("Toi")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Vérifier maintenant" }));
    await waitFor(() => {
      expect(calls.some((c) => c.method === "POST" && c.path === "/v1/audit/verify")).toBe(true);
    });
    expect(screen.queryByRole("button", { name: /Supprimer|Effacer/ })).toBeNull();
  });
});

describe("Réglages du navigateur", () => {
  const state: BrowserState = {
    sources: [{ id: "chrome:Default", label: "Google Chrome — Perso" }],
    grants: [
      { domain: "github.com", source: "connexion", imported_at: "2026-10-15T10:00:00Z", count: 4 },
    ],
    login_domain: null,
  };

  it("n'importe des cookies qu'avec ton accord explicite, domaine par domaine", async () => {
    const { client, calls } = fakeEngine((method, path, body) => {
      if (path === "/v1/browser" && method === "GET") return [200, state];
      if (path === "/v1/browser/cookies" && method === "POST")
        return [
          200,
          { ...(body as object), source: "chrome", imported_at: "2026-10-15T12:00:00Z", count: 3 },
        ];
      if (path === "/v1/settings")
        return [200, { reduced_motion: "system", sphere_quality: "auto" }];
      return [200, []];
    });
    const user = userEvent.setup();
    renderScreen(<SettingsScreen />, { client, path: "/?section=navigateur" });
    const domain = await screen.findByLabelText("Domaine");
    await user.type(domain, "notion.so");
    const submit = screen.getByRole("button", { name: "Importer" });
    expect(submit).toBeDisabled();
    await user.click(screen.getByRole("checkbox", { name: /J'autorise Jarvis/ }));
    await user.click(submit);
    await waitFor(() => {
      const call = calls.find((c) => c.method === "POST" && c.path === "/v1/browser/cookies");
      expect(call?.body).toEqual({ source: "chrome:Default", domain: "notion.so" });
    });
    expect(await screen.findByText("3 cookies de notion.so importés.")).toBeInTheDocument();
  });

  it("révoque un domaine après confirmation", async () => {
    const { client, calls } = fakeEngine((method, path) => {
      if (path === "/v1/browser" && method === "GET") return [200, state];
      if (method === "DELETE") return [204, null];
      if (path === "/v1/settings")
        return [200, { reduced_motion: "system", sphere_quality: "auto" }];
      return [200, []];
    });
    const user = userEvent.setup();
    renderScreen(<SettingsScreen />, { client, path: "/?section=navigateur" });
    const list = await screen.findByRole("list", { name: "Domaines autorisés" });
    expect(list).toHaveTextContent("github.com");
    expect(list).toHaveTextContent("connexion dans sa fenêtre · 4 cookies");
    await user.click(within(list).getByRole("button", { name: "Révoquer" }));
    const dialog = await screen.findByRole("alertdialog");
    await user.click(within(dialog).getByRole("button", { name: "Révoquer" }));
    await waitFor(() => {
      expect(
        calls.some((c) => c.method === "DELETE" && c.path === "/v1/browser/cookies/github.com"),
      ).toBe(true);
    });
  });
});
