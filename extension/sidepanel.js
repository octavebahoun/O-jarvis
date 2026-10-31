// Le panneau de Jarvis dans Chrome : une conversation à côté de l'onglet ouvert. L'onglet n'est
// lu que sur les sites que tu autorises ; chaque plan attend ta validation, sauf en mode « sans
// demander » sur un site où tu l'as permis (actions d'onglet seulement, rien de sensible).
// Les actions elles-mêmes sont faites par le fond de l'extension (background.js).
import { element } from "./dom.js";
import { renderMarkdown } from "./markdown.js";
import { Jarvis } from "./native.js";
import { activeTab, allowSite, originOf, readTab } from "./page.js";
import { planCard, runsAlone } from "./plans.js";

const thread = document.querySelector("#thread");
const form = document.querySelector("#composer");
const input = document.querySelector("#text");
const sendButton = document.querySelector("#send");
const status = document.querySelector("#status");
const tabLine = document.querySelector("#tab");
const withPage = document.querySelector("#with-page");
const yolo = document.querySelector("#yolo");
const stopButton = document.querySelector("#stop");

/** Suites automatiques au plus après une demande (lire, agir, relire…), puis Jarvis s'arrête. */
const MAX_FOLLOW_UPS = 8;

let conversationId = null;
let currentTab = null;
/** L'onglet que Jarvis a lu en dernier : c'est là qu'il agit, même si tu regardes ailleurs. */
let workTab = null;
let workOrigin = null;
let busy = false;
/** La demande en cours et ses suites : arrêtée par le bouton Stop. */
let run = { followUps: 0, stopped: true, plan: null };
let aloneSites = new Set();

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

function scrollDown() {
  thread.scrollTop = thread.scrollHeight;
}

function addNote(text, tone = "", before = null) {
  thread.insertBefore(element("p", `note ${tone}`, text), before);
  scrollDown();
}

function setRunning(running) {
  stopButton.hidden = !running;
}

// --------------------------------------------------------------------------- onglet et mode

async function refreshTab() {
  currentTab = await activeTab();
  const origin = currentTab && originOf(currentTab.url ?? "");
  tabLine.textContent = origin ? `Onglet : ${currentTab.title}` : "Onglet : page interne, non lue";
  yolo.disabled = !origin;
  yolo.checked = Boolean(origin && aloneSites.has(origin));
}

chrome.tabs.onActivated.addListener(refreshTab);
chrome.tabs.onUpdated.addListener((tabId, change) => {
  if (tabId === currentTab?.id && (change.title || change.url)) refreshTab();
});
chrome.storage.local.get({ alone: [] }).then(({ alone }) => {
  aloneSites = new Set(alone);
  refreshTab();
});

yolo.addEventListener("change", async () => {
  const origin = originOf(currentTab?.url ?? "");
  if (!origin) return;
  // Agir sur un site suppose de pouvoir le lire : Chrome le demande pendant ce clic.
  const granted = yolo.checked ? await allowSite(origin).catch(() => false) : true;
  if (yolo.checked && granted) aloneSites.add(origin);
  else aloneSites.delete(origin);
  yolo.checked = aloneSites.has(origin);
  await chrome.storage.local.set({ alone: [...aloneSites] });
  addNote(
    yolo.checked
      ? `Mode sans demander sur ${origin} : clics et champs faits directement ; paiement, achat, suppression et mots de passe restent à valider.`
      : `Mode sans demander coupé sur ${origin}.`,
  );
});

stopButton.addEventListener("click", () => {
  run.stopped = true;
  if (run.plan) jarvis.ask({ type: "stop", plan_id: run.plan }).catch(() => undefined);
  addNote("Arrêté : Jarvis ne fait plus rien dans l'onglet.");
  setRunning(false);
});

// --------------------------------------------------------------------------- conversation

function finish(reply) {
  reply.classList.remove("streaming");
  if (!reply.textContent.trim()) reply.remove();
  busy = false;
  sendButton.disabled = false;
  if (!run.plan) setRunning(false);
}

/** Après un plan fait dans l'onglet : Jarvis relit la page et continue, s'il le faut. */
async function followUp(planId, outcome) {
  run.plan = null;
  if (run.stopped || ["refused", "stopped"].includes(outcome)) {
    setRunning(false);
    return;
  }
  if (run.followUps >= MAX_FOLLOW_UPS) {
    addNote("J'ai assez enchaîné pour cette demande : dis-moi si je continue.");
    setRunning(false);
    return;
  }
  run.followUps += 1;
  addNote("↻ Jarvis relit l'onglet et continue.");
  await send("Continue.", workTab, { afterPlan: planId });
}

function onPlan(plan, before) {
  const alone = runsAlone(plan, workOrigin !== null && aloneSites.has(workOrigin));
  const card = planCard(plan, {
    jarvis,
    alone,
    onRun: (planId) => {
      run.plan = planId;
      setRunning(true);
    },
    onFinished: (planId, outcome) => followUp(planId, outcome),
  });
  thread.insertBefore(card, before);
  scrollDown();
}

async function send(text, tabId, { afterPlan = null } = {}) {
  busy = true;
  sendButton.disabled = true;
  setRunning(true);
  if (!afterPlan) thread.append(element("div", "message user", text));
  const reply = element("div", "message jarvis streaming");
  thread.append(reply);
  scrollDown();
  let page = null;
  if (tabId !== null) {
    page = await readTab(tabId).catch(() => null);
    // Son numéro : les actions validées y seront faites (par le fond de l'extension).
    if (page) page.tab_id = tabId;
    if (page) {
      workTab = tabId;
      workOrigin = originOf(page.url);
    } else addNote("Je n'ai pas pu lire cet onglet : je réponds sans lui.", "", reply);
  }
  let said = "";
  jarvis.request(
    {
      type: "chat",
      text,
      ...(conversationId ? { conversation_id: conversationId } : {}),
      ...(page ? { page } : {}),
      ...(afterPlan ? { after_plan: [afterPlan] } : {}),
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
        } else if (kind === "plan") onPlan(data, reply);
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
  input.value = "";
  run = { followUps: 0, stopped: false, plan: null };
  const tab = currentTab;
  const origin = withPage.checked ? originOf(tab?.url ?? "") : null;
  // Dans le clic même : Chrome ne demande l'accès à un site qu'à ce moment-là.
  const access = origin ? allowSite(origin) : Promise.resolve(false);
  busy = true;
  access
    .catch(() => false)
    .then((granted) => {
      if (origin && !granted)
        addNote("Sans ton accord pour ce site, je réponds sans lire l'onglet.");
      return send(text, granted ? tab.id : null);
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
  workTab = null;
  workOrigin = null;
  run = { followUps: 0, stopped: true, plan: null };
  thread.replaceChildren();
  setRunning(false);
  input.focus();
});
