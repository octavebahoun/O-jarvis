import { screen, waitFor, within } from "@testing-library/react";
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

const BASETEN = { ...KEY, name: "BASETEN_API_KEY", label: "Baseten", configured: false };

describe("L'accueil du premier lancement", () => {
  it("montre chaque étape, faite ou à faire, et ne revient plus une fois passé", async () => {
    const user = userEvent.setup();
    const { client, calls } = fakeEngine((method, path) => {
      if (path === "/v1/secrets") return [200, [KEY, BASETEN]];
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
    expect(await within(step("Tes clés")).findByLabelText("Fait")).toBeInTheDocument();
    // La clé Baseten (Muse Spark Open) est proposée, facultative.
    expect(within(step("Tes clés")).getByText(/clé Baseten est facultative/)).toBeInTheDocument();
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

  it("rallume la voix coupée pour télécharger les voix de Meta", async () => {
    const user = userEvent.setup();
    const { client, calls } = fakeEngine((method, path, body) => {
      if (path === "/v1/settings" && method === "PATCH") return [200, body];
      if (path === "/v1/settings")
        return [200, { model_profile: "meta", os_control_enabled: false, voice_enabled: false }];
      if (path === "/v1/local-models")
        return [
          200,
          [
            {
              key: "voix_meta",
              label: "Voix de Meta",
              folder: "",
              ready: false,
              state: "absent",
              files: [],
            },
          ],
        ];
      return [200, []];
    });
    useOnboarding.setState({ open: true });
    renderScreen(<Onboarding />, { client });
    const button = await screen.findByRole("button", { name: "Télécharger les voix" });
    await waitFor(() => {
      expect(button).toBeEnabled();
    });
    await user.click(button);
    expect(calls).toContainEqual({
      method: "PATCH",
      path: "/v1/settings",
      body: { voice_enabled: true },
    });
    expect(calls.some((c) => c.path === "/v1/local-models/retry")).toBe(false);
  });
});
