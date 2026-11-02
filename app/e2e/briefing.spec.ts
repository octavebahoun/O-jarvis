import { mkdirSync } from "node:fs";
import { resolve } from "node:path";

import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

const CAPTURES = resolve(import.meta.dirname, "../../artefacts/captures");
mkdirSync(CAPTURES, { recursive: true });

async function noSeriousA11yIssues(page: Page) {
  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"])
    .analyze();
  const serious = results.violations.filter(
    (v) => v.impact === "serious" || v.impact === "critical",
  );
  expect(
    serious.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`),
  ).toEqual([]);
}

test("le briefing du jour se joue à la première session, puis se réécoute à la demande", async ({
  page,
}) => {
  await page.goto("/");
  await expect(page.getByText("Moteur connecté")).toBeVisible();
  await expect(page.getByRole("status", { name: "Chargement d'O-jarvis" })).toBeHidden({
    timeout: 20_000,
  });
  // La première session du jour de ce moteur (quel que soit le test qui l'a ouverte) a
  // laissé son briefing dans l'historique.
  await page.getByRole("link", { name: "Historique" }).click();
  await expect(
    page
      .getByRole("list", { name: "Conversations" })
      .getByText(/^Briefing du /)
      .first(),
  ).toBeVisible({ timeout: 15_000 });

  await page.getByRole("link", { name: "Réglages" }).click();
  await page.getByRole("tab", { name: "Intégrations" }).click();
  const integrations = page.getByRole("list", { name: "Intégrations" });
  await integrations.scrollIntoViewIfNeeded();
  await expect(integrations.getByText(/GitHub/)).toBeVisible();
  await expect(integrations.getByText(/Clé absente/).first()).toBeVisible();
  await expect(integrations.getByText(/Ajoute d'abord GOOGLE_CLIENT_ID/)).toBeVisible();
  await expect(integrations.getByText(/Ajoute TELEGRAM_BOT_TOKEN/)).toBeVisible();
  await expect(integrations.getByRole("switch", { name: "Briefing du matin" })).toBeChecked();
  // Le réécouter à la demande : il arrive en notification.
  await integrations.getByRole("button", { name: "Écouter maintenant" }).click();
  await expect(integrations.getByText(/Briefing en préparation/)).toBeVisible();
  await expect(page.getByText(/Briefing du .* — Bonjour ! Briefing de test/)).toBeVisible({
    timeout: 15_000,
  });
  await noSeriousA11yIssues(page);
  await page
    .getByRole("region", { name: "Intégrations et téléphone" })
    .screenshot({ path: `${CAPTURES}/reglages-integrations.png` });
});
