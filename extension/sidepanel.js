// Le panneau de Jarvis dans Chrome : une conversation à côté de l'onglet ouvert. L'onglet n'est
// lu que sur les sites que tu autorises, et chaque plan d'action attend ta validation.
import { renderMarkdown } from "./markdown.js";
import { Jarvis } from "./native.js";
import { activeTab, allowSite, originOf, readTab } from "./page.js";

const thread = document.querySelector("#thread");
const form = document.querySelector("#composer");
const input = document.querySelector("#text");
const sendButton = document.querySelector("#send");
const status = document.querySelector("#status");
const tabLine = document.querySelector("#tab");
const withPage = document.querySelector("#with-page");

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

let conversationId = null;
let currentTab = null;
let busy = false;

function setStatus(tone, text) {
  status.dataset.tone = tone;
  status.textContent = text;
}

const jarvis = new Jarvis({
  onReady: () => setStatus("ok", "Jarvis est prêt"),
  onFatal: (message) => setStatus("down", message),
  onClosed: (reason) =>
    setStatus(
      "down",
      reason.includes("not found")
        ? "Hôte introuvable : lance scripts/install-chrome.sh, puis rouvre le panneau."
        : "Jarvis s'est arrêté : ferme et rouvre le panneau.",
    ),
});

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function scrollDown() {
  thread.scrollTop = thread.scrollHeight;
}

function addNote(text, tone = "", before = null) {
  thread.insertBefore(element("p", `note ${tone}`, text), before);
  scrollDown();
}

async function refreshTab() {
  currentTab = await activeTab();
  const readable = currentTab && originOf(currentTab.url ?? "");
  tabLine.textContent = readable
    ? `Onglet : ${currentTab.title}`
    : "Onglet : page interne, non lue";
}

chrome.tabs.onActivated.addListener(refreshTab);
chrome.tabs.onUpdated.addListener((tabId, change) => {
  if (tabId === currentTab?.id && (change.title || change.url)) refreshTab();
});
refreshTab();

// --------------------------------------------------------------------------- plans

function showPlanState(card, plan) {
  const lines = [STATES[plan.status] ?? plan.status];
  for (const step of plan.steps ?? []) {
    if (step.error) lines.push(`${step.position}. ${step.error}`);
  }
  card.querySelector(".state").textContent = lines.join("\n");
}

async function follow(card, planId, stop) {
  for (;;) {
    const plan = await jarvis.ask({ type: "plan", plan_id: planId });
    showPlanState(card, plan);
    if (FINISHED.has(plan.status)) break;
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
  stop.remove();
}

function addPlan(plan, before) {
  const card = element("section", "plan");
  card.append(element("h3", "", plan.title));
  if (plan.tainted) {
    card.append(element("p", "warning", "Proposé après la lecture d'une page : vérifie-le bien."));
  }
  const steps = element("ol");
  for (const step of plan.steps ?? [])
    steps.append(element("li", `level-${step.level}`, step.description));
  card.append(steps);
  const actions = element("div", "actions");
  const approve = element("button", "primary", "Valider");
  const refuse = element("button", "", "Refuser");
  actions.append(approve, refuse);
  card.append(actions, element("p", "state", "Attend ta validation"));
  let confirming = false;

  approve.addEventListener("click", async () => {
    // Une action risquée (niveau 3) se confirme deux fois, comme dans l'app.
    if (plan.max_level >= 3 && !confirming) {
      confirming = true;
      approve.textContent = "Confirmer : action risquée";
      return;
    }
    approve.disabled = refuse.disabled = true;
    try {
      await jarvis.ask({
        type: "approve",
        plan_id: plan.id,
        double: plan.max_level >= 3,
      });
      const stop = element("button", "", "Arrêter");
      stop.addEventListener("click", () => jarvis.ask({ type: "stop", plan_id: plan.id }));
      actions.replaceChildren(stop);
      await follow(card, plan.id, stop);
    } catch (error) {
      card.querySelector(".state").textContent = error.message;
    }
  });
  refuse.addEventListener("click", async () => {
    approve.disabled = refuse.disabled = true;
    try {
      showPlanState(card, await jarvis.ask({ type: "refuse", plan_id: plan.id }));
    } catch (error) {
      card.querySelector(".state").textContent = error.message;
    }
    actions.remove();
  });
  thread.insertBefore(card, before);
  scrollDown();
}

// --------------------------------------------------------------------------- conversation

function finish(reply) {
  reply.classList.remove("streaming");
  if (!reply.textContent.trim()) reply.remove();
  busy = false;
  sendButton.disabled = false;
  input.focus();
}

async function send(text, tab) {
  thread.append(element("div", "message user", text));
  const reply = element("div", "message jarvis streaming");
  thread.append(reply);
  scrollDown();
  let page = null;
  if (tab) {
    page = await readTab(tab).catch(() => null);
    if (!page) addNote("Je n'ai pas pu lire cet onglet : je réponds sans lui.", "", reply);
  }
  let said = "";
  jarvis.request(
    {
      type: "chat",
      text,
      ...(conversationId ? { conversation_id: conversationId } : {}),
      ...(page ? { page } : {}),
    },
    {
      onEvent: (kind, data) => {
        if (kind === "start") conversationId = data.conversation_id;
        else if (kind === "delta") {
          said += data.text ?? "";
          renderMarkdown(reply, said);
          scrollDown();
        } else if (kind === "tool" && data.status !== "running") {
          addNote(
            `${data.status === "error" ? "✗" : "✓"} ${data.description ?? data.name}`,
            "",
            reply,
          );
        } else if (kind === "plan") addPlan(data, reply);
        else if (kind === "error") addNote(data.message, "error", reply);
      },
      onEnd: () => finish(reply),
      onError: (message) => {
        addNote(message, "error", reply);
        finish(reply);
      },
    },
  );
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  const text = input.value.trim();
  if (!text || busy) return;
  busy = true;
  sendButton.disabled = true;
  input.value = "";
  const tab = currentTab;
  const origin = withPage.checked ? originOf(tab?.url ?? "") : null;
  // Dans le clic même : Chrome ne demande l'accès à un site qu'à ce moment-là.
  const access = origin ? allowSite(origin) : Promise.resolve(false);
  access
    .catch(() => false)
    .then((granted) => {
      if (origin && !granted)
        addNote("Sans ton accord pour ce site, je réponds sans lire l'onglet.");
      return send(text, granted ? tab : null);
    });
});

input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    form.requestSubmit();
  }
});

document.querySelector("#new").addEventListener("click", () => {
  conversationId = null;
  thread.replaceChildren();
  input.focus();
});
