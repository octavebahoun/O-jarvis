import { mkdirSync } from "node:fs";
import { createServer, type Server } from "node:http";
import { resolve } from "node:path";

import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

/**
 * « Jarvis agit », de bout en bout : vraie interface, vrai moteur, vrai navigateur de Jarvis
 * (Chromium piloté par le moteur) sur une boutique locale. Seul le modèle est simulé.
 */
const CAPTURES = resolve(import.meta.dirname, "../../artefacts/captures");
mkdirSync(CAPTURES, { recursive: true });
const SHOP_PORT = 18790;

const PAGES: Record<string, string> = {
  "/boutique":
    "<title>Boutique du thé</title><main><h1>Boutique du thé</h1>" +
    "<form action='/boutique/cherche'><label>Recherche <input name=q></label>" +
    "<button>Chercher</button></form></main>",
  "/boutique/cherche":
    "<title>Résultats</title><h1>Résultats</h1><ul><li>Thé vert sencha — 4,50 €</li></ul>",
};

let shop: Server;

test.beforeAll(async () => {
  shop = createServer((request, response) => {
    const path = (request.url ?? "/").split("?")[0] ?? "/";
    const page = PAGES[path];
    response.writeHead(page ? 200 : 404, { "Content-Type": "text/html; charset=utf-8" });
    response.end(page ?? "introuvable");
  });
  await new Promise<void>((done) => shop.listen(SHOP_PORT, "127.0.0.1", done));
});

test.afterAll(async () => {
  await new Promise<void>((done) =>
    shop.close(() => {
      done();
    }),
  );
});

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

test("un plan multi-étapes avec navigateur s'exécute après validation", async ({ page }) => {
  const field = page.getByLabel("Message pour Jarvis");
  await field.fill(`Va sur http://127.0.0.1:${SHOP_PORT}/boutique et trouve le prix du thé vert.`);
  await field.press("Enter");
  const log = page.getByRole("log", { name: "Conversation" });
  await expect(page).toHaveURL(/c=[0-9a-f]{32}/);
  const conversation = page.url();

  // Lire la page : niveau 1, sans validation. Agir : un plan qui attend ta décision.
  const tools = log.getByRole("list", { name: "Outils utilisés" });
  await expect(tools).toContainText(`Ouvrir http://127.0.0.1:${SHOP_PORT}/boutique`);
  const card = log.getByRole("article", { name: "Chercher le thé vert sur la boutique" });
  await expect(page.getByRole("heading", { name: "Courses à la boutique" })).toBeVisible({
    timeout: 15_000,
  });
  await expect(card).toBeVisible({ timeout: 20_000 });
  await expect(card.getByText("En attente de ta décision")).toBeVisible();
  await expect(card.getByText("Écrire « thé vert » dans le champ « Recherche »")).toBeVisible();
  await expect(card.getByText("Cliquer sur « Chercher » dans la page ouverte")).toBeVisible();
  await expect(card.getByText("Proposé après la lecture")).toBeVisible();
  await expect(page.getByRole("link", { name: /Actions à valider/ })).toContainText("1");
  await noSeriousA11yIssues(page);
  await page.screenshot({ path: `${CAPTURES}/plan-dans-le-chat.png` });

  // L'écran « Actions à valider » montre le même plan.
  await page.getByRole("link", { name: /Actions à valider/ }).click();
  const pending = page.getByRole("list", { name: "Plans à valider" });
  await expect(pending.getByRole("article")).toHaveCount(1);
  await noSeriousA11yIssues(page);
  await page.screenshot({ path: `${CAPTURES}/actions-a-valider.png` });

  await pending.getByRole("button", { name: "Valider" }).click();
  const done = page.getByRole("list", { name: "Plans déjà décidés" }).getByRole("article");
  await expect(done.getByText("Terminé")).toBeVisible({ timeout: 30_000 });
  await expect(done.getByText("C'est fait : le thé vert coûte 4,50 €.")).toBeVisible();
  await expect(done.getByRole("img", { name: "faite" })).toHaveCount(3);
  await page.screenshot({ path: `${CAPTURES}/plan-termine.png` });

  // Le journal des actions garde chaque action, sans bouton pour l'effacer.
  await page.getByRole("link", { name: "Journal et traces" }).click();
  await page.getByRole("tab", { name: "Actions" }).click();
  await expect(page.getByText(/chaîne d'empreintes intacte/)).toBeVisible();
  const entries = page.getByRole("list", { name: "Actions journalisées" });
  await expect(
    entries.getByText("Plan validé : Chercher le thé vert sur la boutique"),
  ).toBeVisible();
  await expect(
    entries.getByText("Fait : Cliquer sur « Chercher » dans la page ouverte"),
  ).toBeVisible();
  await noSeriousA11yIssues(page);
  await page.screenshot({ path: `${CAPTURES}/journal-actions.png` });

  // La synthèse rejoint la conversation.
  await page.goto(conversation);
  await expect(
    page
      .getByRole("log", { name: "Conversation" })
      .getByText("C'est fait : le thé vert coûte 4,50 €.")
      .first(),
  ).toBeVisible();
});

test("refuser un plan est aussi simple que l'accepter", async ({ page }) => {
  const field = page.getByLabel("Message pour Jarvis");
  await field.fill(`Ouvre http://127.0.0.1:${SHOP_PORT}/boutique pour le thé vert.`);
  await field.press("Enter");
  const card = page
    .getByRole("log", { name: "Conversation" })
    .getByRole("article", { name: "Chercher le thé vert sur la boutique" });
  await expect(card.getByRole("button", { name: "Valider" })).toBeVisible({ timeout: 20_000 });
  await card.getByRole("button", { name: "Refuser" }).click();
  await expect(card.getByText("Refusé")).toBeVisible();
  await expect(card.getByRole("button", { name: "Valider" })).toHaveCount(0);
});

test("les réglages montrent le navigateur et les niveaux de permission", async ({ page }) => {
  await page.getByRole("link", { name: "Réglages" }).click();
  // Une section à la fois : la barre des réglages ouvre celle qu'on vérifie.
  await page.getByRole("tab", { name: "Navigateur" }).click();
  await expect(page.getByRole("tabpanel")).toHaveCSS("opacity", "1"); // fin du fondu
  await page.getByRole("heading", { name: "Navigateur et cookies" }).scrollIntoViewIfNeeded();
  await expect(page.getByText("Aucun : Jarvis navigue sans aucune de tes sessions.")).toBeVisible();
  // Dossier personnel jetable : aucun profil de navigateur à importer.
  await expect(page.getByText("Aucun profil Chrome, Chromium ou Brave trouvé.")).toBeVisible();
  await page
    .getByRole("region", { name: "Navigateur et cookies" })
    .screenshot({ path: `${CAPTURES}/reglages-navigateur.png` });
  await page.getByRole("tab", { name: "Permissions" }).click();
  await expect(page.getByRole("tabpanel")).toHaveCSS("opacity", "1"); // fin du fondu
  const writeTools = page.getByRole("list", { name: "Outils de niveau 2" });
  await expect(writeTools.getByText("navigateur_cliquer")).toBeVisible();
  await expect(page.getByRole("list", { name: "Outils de niveau 3" })).toContainText(
    "supprimer_fichier",
  );
  await noSeriousA11yIssues(page);
  await page
    .getByRole("region", { name: "Permissions" })
    .screenshot({ path: `${CAPTURES}/reglages-permissions.png` });
});
