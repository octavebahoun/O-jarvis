import { mkdirSync } from "node:fs";
import { resolve } from "node:path";

import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

/*
 * La voix de bout en bout, avec l'audio simulé du moteur (OJARVIS_FAKE_AUDIO=1) :
 * pas de micro ni de haut-parleur, mais le vrai pipeline, le vrai chat et les vrais
 * événements jusqu'à l'interface.
 */

const CAPTURES = resolve(import.meta.dirname, "../../artefacts/captures");
mkdirSync(CAPTURES, { recursive: true });

async function noSeriousA11yIssues(page: Page) {
  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"])
    .analyze();
  const serious = results.violations.filter(
    (v) => v.impact === "serious" || v.impact === "critical",
  );
  expect(serious.map((v) => v.id)).toEqual([]);
}

test.beforeEach(async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText("Moteur connecté")).toBeVisible();
  await expect(page.getByRole("status", { name: "Chargement d'O-jarvis" })).toBeHidden({
    timeout: 20_000,
  });
});

test("au bouton micro, Jarvis écoute, répond à voix haute puis montre la conversation", async ({
  page,
}) => {
  await expect(page.getByText("Dis « Hey Jarvis », ou écris-moi.")).toBeVisible();
  await page.screenshot({ path: `${CAPTURES}/voix-invite.png` });

  await page.getByRole("button", { name: "Parler à Jarvis" }).click();
  await expect(page.getByRole("img", { name: "Sphère de Jarvis : J'écoute" })).toBeVisible();
  const captions = page.getByRole("group", { name: "Sous-titres" });
  await expect(captions.getByText("Tu as dit : « Quelle heure est-il ? »")).toBeVisible();
  await expect(captions.getByText(/Je suis la réponse de test de Jarvis/)).toBeVisible();
  await page.screenshot({ path: `${CAPTURES}/voix-tour.png` });

  const log = page.getByRole("log", { name: "Conversation" });
  // La question de l'utilisateur (la réponse de test la reprend aussi, en gras).
  await expect(
    log.getByRole("paragraph").filter({ hasText: /^Quelle heure est-il \?$/ }),
  ).toBeVisible({ timeout: 15_000 });
  await expect(log.getByText(/Je suis la réponse de test de Jarvis/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Arrêter la voix" })).toBeVisible();
  await page.getByRole("button", { name: "Arrêter la voix" }).click();
  await expect(page.getByRole("button", { name: "Parler à Jarvis" })).toBeVisible();
  await noSeriousA11yIssues(page);
  await page.screenshot({ path: `${CAPTURES}/voix-conversation.png` });

  await page.getByRole("link", { name: "Historique" }).click();
  // Attendre la page : sur l'accueil, la conversation contient aussi la question.
  const conversations = page.getByRole("list", { name: "Conversations" });
  await expect(
    conversations.getByRole("listitem").filter({ hasText: "Quelle heure est-il ?" }),
  ).toBeVisible();
});

test("les réglages de la voix montrent l'état et changent la sensibilité", async ({ page }) => {
  await page.getByRole("link", { name: "Réglages" }).click();
  await page.getByRole("tab", { name: "Voix", exact: true }).click();
  await expect(page.getByRole("tabpanel")).toHaveCSS("opacity", "1"); // fin du fondu
  const voice = page.getByRole("region", { name: "Voix", exact: true });
  await expect(voice.getByText("À l'écoute de « Hey Jarvis »")).toBeVisible();
  await expect(voice.getByText(/Mot de réveil : simulé/)).toBeVisible();
  await voice.getByRole("radio", { name: /Haute/ }).click();
  await expect(voice.getByRole("radio", { name: /Haute/ })).toBeChecked();
  await voice.scrollIntoViewIfNeeded();
  await noSeriousA11yIssues(page);
  await voice.screenshot({ path: `${CAPTURES}/reglages-voix.png` });
});
