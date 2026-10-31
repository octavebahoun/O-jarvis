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

test.beforeEach(async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText("Moteur connecté")).toBeVisible();
  await expect(page.getByRole("status", { name: "Chargement d'O-jarvis" })).toBeHidden({
    timeout: 20_000,
  });
});

test("une automatisation se crée, se lance, prévient, puis se supprime", async ({ page }) => {
  await page.getByRole("link", { name: "Automatisations" }).click();
  await page.getByRole("button", { name: "Créer une automatisation" }).click();
  const form = page.getByRole("form", { name: "Nouvelle automatisation" });
  await form.getByLabel("Nom").fill("Hydratation");
  await form.getByLabel("Heure", { exact: true }).fill("10:30");
  await form.getByLabel("Message du rappel").fill("Boire un grand verre d'eau.");
  await noSeriousA11yIssues(page);
  await page.screenshot({ path: `${CAPTURES}/automatisation-formulaire.png` });
  await form.getByRole("button", { name: "Enregistrer" }).click();

  const card = page.getByRole("article", { name: "Hydratation" });
  await expect(card.getByText("Tous les jours à 10 h 30")).toBeVisible();
  await expect(card.getByText(/Prochaine fois : .* 10:30/)).toBeVisible();

  await card.getByRole("button", { name: "Lancer maintenant" }).click();
  // Le rappel arrive en notification dans l'app.
  await expect(page.getByText("Hydratation — Boire un grand verre d'eau.")).toBeVisible();
  await card.getByRole("button", { name: "Historique" }).click();
  await expect(card.getByText(/lancée à la main · faite/)).toBeVisible();
  await noSeriousA11yIssues(page);
  await page.screenshot({ path: `${CAPTURES}/automatisations.png` });

  await card.getByRole("switch", { name: "Désactiver Hydratation" }).click();
  await expect(card.getByText("Désactivée")).toBeVisible();
  await expect(card.getByText(/Ne tournera plus/)).toBeVisible();

  await card.getByRole("button", { name: "Supprimer" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Supprimer" }).click();
  await expect(page.getByText("Aucune automatisation")).toBeVisible();

  // Création et suppression sont au journal des actions.
  await page.getByRole("link", { name: "Journal et traces" }).click();
  await page.getByRole("tab", { name: "Actions" }).click();
  await expect(page.getByText(/Automatisation créée : « Hydratation »/)).toBeVisible();
  await expect(page.getByText(/Automatisation supprimée : « Hydratation »/)).toBeVisible();
});
