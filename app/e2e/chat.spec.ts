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

test("une conversation se déroule en direct puis rejoint l'historique", async ({ page }) => {
  const field = page.getByLabel("Message pour Jarvis");
  await field.fill("Bonjour Jarvis");
  await field.press("Enter");
  const log = page.getByRole("log", { name: "Conversation" });
  await expect(log.locator("p", { hasText: /^Bonjour Jarvis$/ })).toBeVisible();
  await expect(log.locator("strong", { hasText: "Bonjour Jarvis" })).toBeVisible();
  await expect(log.getByText(/réponse de test de Jarvis/)).toBeVisible();
  await expect(page).toHaveURL(/c=[0-9a-f]{32}/);
  await expect(page.getByRole("heading", { name: "Conversation de test" })).toBeVisible({
    timeout: 15_000,
  });
  await expect(log.getByText(/deepseek-flash/)).toBeVisible();
  await noSeriousA11yIssues(page);
  await page.screenshot({ path: `${CAPTURES}/chat.png` });

  await field.fill("Et maintenant ?");
  await page.getByRole("button", { name: "Envoyer" }).click();
  await expect(log.getByText(/Bien reçu : Et maintenant/)).toBeVisible();

  await page.getByRole("link", { name: "Historique" }).click();
  const row = page.getByRole("listitem").filter({ hasText: "Conversation de test" });
  await expect(row).toBeVisible();
  await expect(row.getByText(/4 messages/)).toBeVisible();
  await noSeriousA11yIssues(page);
  await page.screenshot({ path: `${CAPTURES}/historique.png` });

  await page.getByLabel("Rechercher dans les conversations").fill("maintenant");
  await expect(row).toBeVisible();
  await page.getByLabel("Rechercher dans les conversations").fill("introuvable");
  await expect(page.getByText("Aucun résultat")).toBeVisible();
  await page.getByLabel("Rechercher dans les conversations").fill("");

  await row.getByRole("button", { name: /Renommer/ }).click();
  // En édition, le titre passe dans le champ : la ligne ne contient plus ce texte.
  const editing = page.getByRole("list", { name: "Conversations" }).getByRole("listitem");
  await editing.getByLabel("Nouveau titre").fill("Salutations");
  await editing.getByRole("button", { name: "Renommer", exact: true }).click();
  const renamed = page.getByRole("listitem").filter({ hasText: "Salutations" });
  await expect(renamed).toBeVisible();
  await renamed
    .getByRole("button", { name: /Salutations/ })
    .first()
    .click();
  await expect(page.getByRole("heading", { name: "Salutations" })).toBeVisible();
  await expect(
    // Le message de l'utilisateur, pas sa reprise en gras dans la réponse de test.
    page
      .getByRole("log", { name: "Conversation" })
      .getByRole("paragraph")
      .filter({ hasText: /^Et maintenant \?$/ }),
  ).toBeVisible();

  await page.getByRole("link", { name: "Historique" }).click();
  await page
    .getByRole("listitem")
    .filter({ hasText: "Salutations" })
    .getByRole("button", { name: /Supprimer/ })
    .click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Supprimer" }).click();
  // D'autres tests ont pu créer leurs conversations : seule celle-ci doit disparaître.
  await expect(page.getByRole("listitem").filter({ hasText: "Salutations" })).toHaveCount(0);
});

test("la mémoire se consulte, s'enrichit, se corrige et oublie", async ({ page }) => {
  await page.getByRole("link", { name: "Mémoire" }).click();
  await expect(page.getByText("Jarvis ne sait encore rien ici")).toBeVisible();
  await page.getByLabel("Type de souvenir").selectOption("preference");
  await page.getByLabel("Ce que Jarvis doit retenir").fill("J'aime le thé à la menthe.");
  await page.getByRole("button", { name: "Retenir" }).click();
  const item = page.getByRole("listitem").filter({ hasText: "thé à la menthe" });
  await expect(item).toBeVisible();
  await expect(item.getByText("Ajouté à la main")).toBeVisible();

  await item.getByRole("button", { name: "J'aime le thé à la menthe." }).click();
  await page.getByLabel("Modifier ce souvenir").fill("J'aime le thé vert.");
  await page.getByRole("button", { name: "Enregistrer" }).click();
  const edited = page.getByRole("listitem").filter({ hasText: "thé vert" });
  await expect(edited).toBeVisible();
  await edited.getByRole("button", { name: /Épingler/ }).click();
  await expect(edited.getByRole("button", { name: /Épingler/, pressed: true })).toBeVisible();
  await expect(edited.getByText(/épinglé/)).toBeVisible();

  await page.getByRole("tab", { name: "Préférences" }).click();
  await expect(edited).toBeVisible();
  await page.getByRole("tab", { name: "Profil" }).click();
  await expect(page.getByText("Jarvis ne sait encore rien ici")).toBeVisible();
  await page.getByRole("tab", { name: "Tout" }).click();
  await page.getByLabel("Rechercher dans la mémoire").fill("vert");
  await expect(edited).toBeVisible();
  await noSeriousA11yIssues(page);
  await page.screenshot({ path: `${CAPTURES}/memoire.png` });

  await edited.getByRole("button", { name: "Oublier ce souvenir" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Oublier" }).click();
  await expect(edited).toHaveCount(0);
});

test("les réglages montrent les modèles et le choix par tâche", async ({ page }) => {
  await page.getByRole("link", { name: "Réglages" }).click();
  await page.getByRole("tab", { name: "Modèles" }).click();
  const providers = page.getByRole("list", { name: "Fournisseurs de modèles" });
  await expect(providers.getByText("DeepSeek")).toBeVisible();
  await expect(providers.getByText("Prêt")).toBeVisible();
  const voice = page.getByRole("combobox", { name: /^Voix/ });
  await expect(voice).toHaveValue("auto");
  await expect(page.getByRole("switch", { name: /Apprendre de mes conversations/ })).toBeChecked();
  await page.getByRole("heading", { name: "Modèles", exact: true }).scrollIntoViewIfNeeded();
  await noSeriousA11yIssues(page);
  await page.screenshot({ path: `${CAPTURES}/reglages-modeles.png` });
});
