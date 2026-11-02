import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { fakeEngine, renderScreen } from "../../test/render";
import { SettingsScreen } from "./SettingsScreen";

describe("Réglages : la barre des sections", () => {
  it("ouvre une section à la fois, les clés d'abord", async () => {
    const user = userEvent.setup();
    renderScreen(<SettingsScreen />, { client: null, stream: "closed" });
    const bar = screen.getByRole("tablist", { name: "Sections des réglages" });
    expect(bar).toHaveAttribute("aria-orientation", "vertical");
    expect(screen.getAllByRole("tab")).toHaveLength(10);
    expect(screen.getByRole("tab", { name: "Clés" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("region", { name: "Fournisseurs et clés" })).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "Apparence" }));
    expect(screen.getByRole("tab", { name: "Apparence" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("region", { name: "Apparence" })).toBeInTheDocument();
    // La section précédente n'est plus là : la page ne défile plus sans fin.
    expect(screen.queryByRole("region", { name: "Fournisseurs et clés" })).not.toBeInTheDocument();
  });

  it("ouvre la section demandée par l'adresse, et les clés si elle n'existe pas", () => {
    const { unmount } = renderScreen(<SettingsScreen />, {
      client: null,
      stream: "closed",
      path: "/?section=controle-pc",
    });
    expect(screen.getByRole("region", { name: "Contrôle du PC" })).toBeInTheDocument();
    unmount();
    renderScreen(<SettingsScreen />, {
      client: null,
      stream: "closed",
      path: "/?section=inconnue",
    });
    expect(screen.getByRole("region", { name: "Fournisseurs et clés" })).toBeInTheDocument();
  });
});

describe("Réglages : la lecture de l'écran en « Tout Meta »", () => {
  it("dit que Muse Spark lit l'écran en ligne, sans choix à faire", async () => {
    const { client } = fakeEngine((_method, path) => {
      if (path === "/v1/settings")
        return [200, { os_control_enabled: true, screen_vision: "local", model_profile: "meta" }];
      return [200, []];
    });
    renderScreen(<SettingsScreen />, { client, path: "/?section=controle-pc" });
    expect(await screen.findByText(/en ligne, par Muse Spark/)).toBeInTheDocument();
    expect(screen.queryByRole("radio", { name: /Sur ce PC/ })).not.toBeInTheDocument();
  });
});
