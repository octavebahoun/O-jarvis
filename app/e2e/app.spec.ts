import { mkdirSync } from "node:fs";
import { resolve } from "node:path";

import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

const CAPTURES = resolve(import.meta.dirname, "../../artefacts/captures");
mkdirSync(CAPTURES, { recursive: true });

async function capture(page: Page, name: string) {
  await page.screenshot({ path: `${CAPTURES}/${name}.png`, fullPage: false });
}

async function expectNoSeriousA11yIssues(page: Page) {
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

test("l'accueil montre la sphère et l'état du moteur", async ({ page }) => {
  await expect(page.getByRole("img", { name: /Sphère de Jarvis/ })).toBeVisible();
  await expect(page.getByText("Dis « Hey Jarvis », ou écris-moi.")).toBeVisible();
  await expectNoSeriousA11yIssues(page);
  await capture(page, "accueil");
});

test("chaque état de la sphère se prévisualise et s'annonce", async ({ page }) => {
  const states: [string, string][] = [
    ["En veille", "veille"],
    ["J'écoute", "ecoute"],
    ["Je réfléchis", "reflexion"],
    ["Je parle", "parole"],
    ["Action en attente de ta validation", "validation"],
    ["Un problème est survenu", "erreur"],
  ];
  for (const [label, file] of states) {
    await page.getByRole("button", { name: label, exact: true }).click();
    await expect(page.getByRole("img", { name: `Sphère de Jarvis : ${label}` })).toBeVisible();
    await page.waitForTimeout(900);
    await capture(page, `sphere-${file}`);
  }
});

test("une clé s'ajoute, se vérifie, laisse une trace, puis se supprime", async ({ page }) => {
  await page.getByRole("link", { name: "Réglages" }).click();
  await expect(page.getByRole("heading", { name: "Réglages", level: 1 })).toBeVisible();
  await expectNoSeriousA11yIssues(page);
  await capture(page, "reglages");

  const row = page.getByRole("listitem").filter({ hasText: "Tavily" });
  await row.getByRole("button", { name: "Ajouter" }).click();
  const field = row.getByLabel("Nouvelle valeur pour Tavily");
  await field.fill("valeur-de-test-e2e");
  await row.getByRole("button", { name: "Enregistrer" }).click();
  await expect(row.getByText("Configurée, saisie ici")).toBeVisible();
  await expect(row.getByText(/Vérifiée|Refusée/)).toBeVisible({ timeout: 20_000 });
  await expect(page.locator("body")).not.toContainText("valeur-de-test-e2e");

  await page.getByRole("link", { name: "Journal et traces" }).click();
  const trace = page.getByRole("button", { name: /Vérification Tavily/ });
  await expect(trace).toBeVisible();
  await trace.click();
  await expect(page.getByRole("heading", { name: "Vérification Tavily" })).toBeVisible();
  await expect(page.getByText("appel fournisseur")).toBeVisible();
  await expectNoSeriousA11yIssues(page);
  await capture(page, "journal");

  await page.getByRole("link", { name: "Réglages" }).click();
  const again = page.getByRole("listitem").filter({ hasText: "Tavily" });
  await again.getByRole("button", { name: "Supprimer" }).click();
  const dialog = page.getByRole("alertdialog");
  await expect(dialog).toBeVisible();
  await capture(page, "reglages-confirmation");
  await dialog.getByRole("button", { name: "Supprimer" }).click();
  await expect(again.getByText("Non configurée")).toBeVisible();
});

test("la navigation se fait entièrement au clavier", async ({ page }) => {
  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "Aller au contenu" })).toBeFocused();
  for (const name of [
    "Accueil",
    "Historique",
    "Mémoire",
    "Actions à valider",
    "Automatisations",
    "Agents",
    "Skills et plugins",
    "Journal et traces",
    "Réglages",
  ]) {
    await page.keyboard.press("Tab");
    await expect(page.getByRole("link", { name })).toBeFocused();
  }
  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { name: "Réglages", level: 1 })).toBeVisible();
});

test("pendant le splash, le clavier ne part pas dans l'app", async ({ page }) => {
  await page.reload();
  await expect(page.getByRole("status", { name: "Chargement d'O-jarvis" })).toBeVisible();
  await page.keyboard.press("Tab");
  const focused = await page.evaluate(
    () => document.activeElement === null || document.activeElement === document.body,
  );
  expect(focused).toBe(true);
});

test("la fenêtre reste utilisable à 900 px de large", async ({ page }) => {
  await page.setViewportSize({ width: 900, height: 700 });
  await expect(page.getByRole("link", { name: "Réglages" })).toBeVisible();
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth > window.innerWidth,
  );
  expect(overflow).toBe(false);
  await capture(page, "accueil-900");
  await page.getByRole("link", { name: "Réglages" }).click();
  await expect(page.getByRole("heading", { name: "Réglages", level: 1 })).toBeVisible();
  await capture(page, "reglages-900");
});

test("les animations réduites gardent l'information", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.reload();
  await page.getByRole("button", { name: "Action en attente de ta validation" }).click();
  await expect(
    page.getByText("Action en attente de ta validation", { exact: true }).last(),
  ).toBeVisible();
  await page.waitForTimeout(600);
  await capture(page, "sphere-validation-reduit");
});
