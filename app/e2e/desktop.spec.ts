import { mkdirSync } from "node:fs";
import { resolve } from "node:path";

import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

/** Contrôle du PC : désactivé par défaut, activé seulement après confirmation, journalisé. */
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

test("le contrôle du PC est désactivé par défaut et ne s'active qu'après confirmation", async ({
  page,
}) => {
  await page.getByRole("link", { name: "Réglages" }).click();
  await page.getByRole("tab", { name: "Contrôle du PC" }).click();
  await expect(page.getByRole("tabpanel")).toHaveCSS("opacity", "1"); // fin du fondu
  const section = page.getByRole("region", { name: "Contrôle du PC" });
  await section.scrollIntoViewIfNeeded();
  await expect(section.getByText(/Désactivé : Jarvis ne voit/)).toBeVisible();
  const local = section.getByRole("radio", { name: /Sur ce PC/ });
  await expect(local).toBeChecked(); // l'écran reste sur la machine par défaut
  await expect(local).toBeDisabled();
  const openApp = page
    .getByRole("list", { name: "Outils de niveau 2" })
    .getByRole("listitem")
    .filter({ hasText: "ouvrir_application" });
  await page.getByRole("tab", { name: "Permissions" }).click();
  await expect(openApp).toContainText("Contrôle du PC (désactivé)");
  await page.getByRole("tab", { name: "Contrôle du PC" }).click();
  await expect(page.getByRole("tabpanel")).toHaveCSS("opacity", "1"); // fin du fondu

  // Annuler ne change rien.
  await section.getByRole("button", { name: "Activer…" }).click();
  const dialog = page.getByRole("alertdialog", { name: "Activer le contrôle du PC ?" });
  await expect(dialog).toContainText("Rien ne se fait sans ta validation");
  await noSeriousA11yIssues(page);
  await page.screenshot({ path: `${CAPTURES}/controle-pc-confirmation.png` });
  await dialog.getByRole("button", { name: "Annuler" }).click();
  await expect(section.getByText(/Désactivé : Jarvis ne voit/)).toBeVisible();

  await section.getByRole("button", { name: "Activer…" }).click();
  await dialog.getByRole("button", { name: "Activer" }).click();
  await expect(section.getByText(/Activé : Jarvis peut/)).toBeVisible();
  await expect(local).toBeEnabled();
  await page.getByRole("tab", { name: "Permissions" }).click();
  await expect(openApp).not.toContainText("(désactivé)");
  await page.getByRole("tab", { name: "Contrôle du PC" }).click();
  await expect(page.getByRole("tabpanel")).toHaveCSS("opacity", "1"); // fin du fondu
  await noSeriousA11yIssues(page);
  await section.screenshot({ path: `${CAPTURES}/reglages-controle-pc.png` });

  // Le désactiver est immédiat.
  await section.getByRole("button", { name: "Désactiver" }).click();
  await expect(section.getByText(/Désactivé : Jarvis ne voit/)).toBeVisible();

  // Chaque changement de cette permission est au journal des actions.
  await page.getByRole("link", { name: "Journal et traces" }).click();
  await page.getByRole("tab", { name: "Actions" }).click();
  const entries = page.getByRole("list", { name: "Actions journalisées" });
  await expect(entries.getByText("Contrôle du PC activé")).toBeVisible();
  await expect(entries.getByText("Contrôle du PC désactivé")).toBeVisible();
});
