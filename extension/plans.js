// Les plans de Jarvis dans le panneau : validés par toi, ou faits sans demander sur un site où
// tu l'as permis, si toutes leurs étapes restent dans l'onglet et qu'aucune n'est sensible.
import { element } from "./dom.js";

const FINISHED = new Set(["done", "failed", "refused", "interrupted", "stopped"]);
const STATES = {
  approved: "Validé, en attente",
  running: "En cours…",
  done: "Terminé",
  failed: "Échec",
  refused: "Refusé",
  interrupted: "Interrompu",
  stopped: "Arrêté",
};

/** Le mode sans demander s'applique : rien de niveau 3, et seulement des actions d'onglet. */
export function runsAlone(plan, siteAllowed) {
  const steps = plan.steps ?? [];
  return (
    siteAllowed &&
    plan.max_level <= 2 &&
    steps.length > 0 &&
    steps.every((step) => String(step.tool ?? "").startsWith("onglet_"))
  );
}

function showState(card, plan) {
  const lines = [STATES[plan.status] ?? plan.status];
  for (const step of plan.steps ?? []) {
    if (step.error) lines.push(`${step.position}. ${step.error}`);
  }
  card.querySelector(".state").textContent = lines.join("\n");
}

/** La carte d'un plan. `onRun` reçoit son identifiant quand il démarre ; `onFinished`, son
 *  état final. */
export function planCard(plan, { jarvis, alone, onRun, onFinished }) {
  const card = element("section", "plan");
  card.append(element("h3", "", plan.title));
  if (alone) card.append(element("p", "alone", "Fait sans demander : mode permis sur ce site."));
  else if (plan.tainted) {
    card.append(element("p", "warning", "Proposé après la lecture d'une page : vérifie-le bien."));
  }
  const steps = element("ol");
  for (const step of plan.steps ?? []) {
    steps.append(element("li", `level-${step.level}`, step.description));
  }
  const actions = element("div", "actions");
  card.append(steps, actions, element("p", "state", "Attend ta validation"));

  async function follow() {
    for (;;) {
      const current = await jarvis.ask({ type: "plan", plan_id: plan.id });
      showState(card, current);
      if (FINISHED.has(current.status)) return current.status;
      await new Promise((resolve) => setTimeout(resolve, 800));
    }
  }

  async function approve(double) {
    try {
      await jarvis.ask({ type: "approve", plan_id: plan.id, double });
      const stop = element("button", "", "Arrêter");
      stop.addEventListener("click", () => jarvis.ask({ type: "stop", plan_id: plan.id }));
      actions.replaceChildren(stop);
      onRun(plan.id);
      const status = await follow();
      actions.remove();
      onFinished(plan.id, status);
    } catch (error) {
      card.querySelector(".state").textContent = error.message;
      onFinished(plan.id, "failed");
    }
  }

  if (alone) {
    approve(false);
    return card;
  }
  const yes = element("button", "primary", "Valider");
  const no = element("button", "", "Refuser");
  actions.append(yes, no);
  let confirming = false;
  yes.addEventListener("click", () => {
    // Une action sensible (niveau 3) se confirme deux fois, comme dans l'app.
    if (plan.max_level >= 3 && !confirming) {
      confirming = true;
      yes.textContent = "Confirmer : action sensible";
      return;
    }
    yes.disabled = no.disabled = true;
    approve(plan.max_level >= 3);
  });
  no.addEventListener("click", async () => {
    yes.disabled = no.disabled = true;
    try {
      showState(card, await jarvis.ask({ type: "refuse", plan_id: plan.id }));
    } catch (error) {
      card.querySelector(".state").textContent = error.message;
    }
    actions.remove();
    onFinished(plan.id, "refused");
  });
  return card;
}
