import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { fakeEngine, renderScreen } from "../../test/render";
import { Onboarding } from "./Onboarding";
import { useOnboarding } from "./store";

const KEY = {
  name: "MODEL_API_KEY",
  label: "Meta",
  group: "llm",
  help: "",
  configured: true,
  source: "ui",
  updated_at: null,
  last_check_ok: true,
  last_check_at: null,
  last_check_detail: null,
  last_check_latency_ms: null,
};

describe("L'accueil du premier lancement", () => {
  it("montre chaque étape, faite ou à faire, et ne revient plus une fois passé", async () => {
    const user = userEvent.setup();
    const { client, calls } = fakeEngine((method, path) => {
      if (path === "/v1/secrets") return [200, [KEY]];
      if (path === "/v1/settings")
        return [200, { model_profile: "meta", os_control_enabled: false }];
      if (path === "/v1/local-models")
        return [
          200,
          [
            {
              key: "voix_meta",
              label: "Voix de Meta",
              folder: "",
              ready: true,
              state: "prêt",
              files: [],
            },
          ],
        ];
      if (path === "/v1/chrome/status") return [200, { connected: true }];
      if (path === "/v1/desktop/pointer/authorize" && method === "POST")
        return [200, { authorized: true }];
      return [200, []];
    });
    useOnboarding.setState({ open: true });
    renderScreen(<Onboarding />, { client });
    const dialog = await screen.findByRole("dialog", { name: "Bienvenue dans O-jarvis" });
    const step = (name: string) =>
      within(dialog)
        .getByRole("heading", { name: new RegExp(name) })
        .closest("li") as HTMLElement;
    expect(await within(step("Ta clé Meta")).findByLabelText("Fait")).toBeInTheDocument();
    expect(within(step("Tout Meta")).getByLabelText("Fait")).toBeInTheDocument();
    expect(
      await within(step("Chrome")).findByText("Extension branchée sur ce moteur."),
    ).toBeInTheDocument();
    // Le contrôle du PC s'active d'abord ; les accords de GNOME viennent après.
    expect(
      within(step("contrôle du PC")).getByRole("button", { name: /Accord pour cliquer/ }),
    ).toBeDisabled();
    await user.click(within(dialog).getByRole("button", { name: "Passer" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(localStorage.getItem("ojarvis.accueil.vu")).toBe("1");
    expect(calls.some((c) => c.path === "/v1/desktop/pointer/authorize")).toBe(false);
  });
});
