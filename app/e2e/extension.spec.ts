import { mkdirSync } from "node:fs";
import { resolve } from "node:path";

import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

/** Jarvis s'étend : agents, skills et serveurs MCP, mémoire graphe — vraie interface, vrai moteur. */
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

test("un agent prend une mission confiée depuis l'écran et la rend", async ({ page }) => {
  await page.getByRole("link", { name: "Agents" }).click();
  await expect(page.getByRole("heading", { name: "Agents", level: 1 })).toBeVisible();
  const coach = page.getByRole("article", { name: "Coach d'apprentissage" });
  await expect(coach.getByText("Disponible")).toBeVisible();
  await coach.getByLabel("Lui confier une tâche").fill("Explique-moi les closures en JavaScript.");
  await coach.getByRole("button", { name: "Confier" }).click();
  const history = page.getByRole("list", { name: "Missions récentes" });
  await expect(history.getByText("Terminée")).toBeVisible();
  await expect(history.getByText(/Je suis la réponse de test/)).toBeVisible();
  await expect(history.getByText(/confiée par toi/)).toBeVisible();
  await noSeriousA11yIssues(page);
  await page.screenshot({ path: `${CAPTURES}/agents.png` });
});

test("skills et serveurs MCP : rien d'actif sans toi, recherche prête", async ({ page }) => {
  await page.getByRole("link", { name: "Skills et plugins" }).click();
  await expect(page.getByRole("heading", { name: "Skills et plugins", level: 1 })).toBeVisible();
  await expect(page.getByText("Aucun skill pour l'instant")).toBeVisible();
  await expect(page.getByText("Aucun serveur installé")).toBeVisible();
  await expect(page.getByLabel("Chercher dans le registre MCP officiel")).toBeVisible();
  await noSeriousA11yIssues(page);
  await page.screenshot({ path: `${CAPTURES}/skills-et-plugins.png` });
});

test("le graphe de la mémoire se complète et se relie à la main", async ({ page }) => {
  await page.getByRole("link", { name: "Mémoire" }).click();
  await page.getByRole("tab", { name: "Graphe" }).click();
  await expect(page.getByText("Le graphe est vide")).toBeVisible();
  const add = page.getByRole("form", { name: "Ajouter une entité" });
  for (const [name, kind] of [
    ["Thèse", "projet"],
    ["Pr Adjovi", "personne"],
  ] as const) {
    await add.getByPlaceholder("Nom").fill(name);
    await add.getByLabel("Type").selectOption(kind);
    await add.getByRole("button", { name: "Ajouter" }).click();
    await expect(page.getByRole("list", { name: "Entités" }).getByText(name)).toBeVisible();
  }
  await page
    .getByRole("list", { name: "Entités" })
    .getByRole("button", { name: /Pr Adjovi/ })
    .click();
  const details = page.getByRole("region", { name: "Pr Adjovi" });
  await details.getByLabel("Nouveau lien").fill("dirige");
  await details.getByLabel("Relier à").selectOption({ label: "Thèse" });
  await details.getByRole("button", { name: "Relier" }).click();
  await expect(details.getByRole("list", { name: "Liens de Pr Adjovi" })).toContainText(
    "Pr Adjovi dirige Thèse",
  );
  await noSeriousA11yIssues(page);
  await page.screenshot({ path: `${CAPTURES}/memoire-graphe.png` });
});
