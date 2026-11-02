// Les actions validées, faites dans ton onglet : cliquer, remplir, ouvrir. Chacune vérifie
// que l'élément est bien celui du plan, et refuse un mot de passe, une carte bancaire, ou une
// action sensible (paiement, achat, suppression) qui n'a pas été validée au niveau 3.
import { readTab } from "./page.js";

/** Exécuté dans la page, avec l'action en argument (rien d'autre n'y est disponible). Avant
 *  le geste, le curseur d'O-jarvis glisse jusqu'à l'élément, qui s'entoure d'un halo : on voit
 *  ce que Jarvis fait. Le geste reste celui du plan validé, vérifié avant toute animation. */
async function performInPage(action) {
  const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  /** Le curseur glisse jusqu'à la cible, puis le halo et l'étiquette disent le geste. */
  const showGesture = async (rect, text) => {
    const state = (window.__ojarvisCursor ??= {});
    const make = (tag, css) => {
      const element = document.createElement(tag);
      element.style.cssText =
        "position:fixed;pointer-events:none;z-index:2147483647;left:0;top:0;" + css;
      document.documentElement.append(element);
      return element;
    };
    if (!state.cursor?.isConnected) {
      state.cursor = make("div", "width:26px;height:26px;transition:transform .6s cubic-bezier(.2,.8,.2,1),opacity .3s;filter:drop-shadow(0 2px 4px rgba(0,0,0,.45))");
      state.cursor.innerHTML =
        '<svg viewBox="0 0 24 24" width="26" height="26"><path d="M3 2l7 19 2.6-7.4L20 11z" fill="#14b8a6" stroke="#fff" stroke-width="1.6" stroke-linejoin="round"/></svg>';
      state.halo = make("div", "border:3px solid #14b8a6;border-radius:10px;box-shadow:0 0 0 6px rgba(20,184,166,.25);opacity:0;transition:opacity .25s");
      state.label = make("div", "background:#0f172a;color:#fff;font:600 13px/1.3 system-ui,sans-serif;padding:6px 10px;border-radius:999px;opacity:0;transition:opacity .25s;white-space:nowrap");
      const start = state.at ?? { x: innerWidth / 2, y: innerHeight / 2 };
      state.cursor.style.transform = `translate(${start.x}px, ${start.y}px)`;
      await pause(30);
    }
    clearTimeout(state.timer);
    state.cursor.style.opacity = "1";
    const x = rect.left + rect.width / 2;
    const y = rect.top + rect.height / 2;
    state.cursor.style.transform = `translate(${x}px, ${y}px)`;
    state.at = { x, y };
    await pause(650);
    Object.assign(state.halo.style, {
      left: `${rect.left - 6}px`,
      top: `${rect.top - 6}px`,
      width: `${rect.width + 12}px`,
      height: `${rect.height + 12}px`,
      opacity: "1",
    });
    state.label.textContent = `O-jarvis · ${text}`;
    Object.assign(state.label.style, {
      left: `${Math.min(rect.left, innerWidth - 320)}px`,
      top: `${rect.top > 44 ? rect.top - 40 : rect.bottom + 12}px`,
      opacity: "1",
    });
    await pause(350);
    state.timer = setTimeout(() => {
      state.halo.style.opacity = "0";
      state.label.style.opacity = "0";
      state.timer = setTimeout(() => (state.cursor.style.opacity = "0"), 2500);
    }, 900);
  };
  /** Écrit le texte lettre par lettre (deux secondes au plus), comme une personne. */
  const typeInto = async (write, text) => {
    const steps = Math.min(text.length, 120);
    for (let i = 1; i <= steps; i += 1) {
      write(text.slice(0, Math.round((text.length * i) / steps)));
      await pause(16);
    }
  };
  const risky =
    /pa(?:y|yer|iement)|achet|achat|command|checkout|\bbuy|order|purchase|abonn|subscri|supprim|effac|delete|remove|résili|virement|transf|mot de passe|password|carte|card|cvv|cvc|iban|bancaire/i;
  const fold = (text) =>
    String(text ?? "")
      .normalize("NFKD")
      .replace(/[̀-ͯ]/g, "")
      .replace(/\s+/g, " ")
      .trim()
      .toLowerCase();
  const labelOf = (node) =>
    node.getAttribute("aria-label") ||
    (node.labels?.length ? [...node.labels].map((label) => label.innerText).join(" ") : "") ||
    (["submit", "button", "reset"].includes(node.type) ? node.value : "") ||
    node.innerText ||
    node.getAttribute("placeholder") ||
    node.getAttribute("title") ||
    node.querySelector?.("img[alt]")?.alt ||
    node.getAttribute("name") ||
    "";

  const { kind, args, level } = action;
  const found = document.querySelectorAll(`[data-ojarvis-ref="${Number(args.ref)}"]`);
  if (found.length !== 1) {
    return { ok: false, text: "Cet élément n'est plus dans la page : relis-la avant d'agir." };
  }
  const node = found[0];
  const label = labelOf(node).replace(/\s+/g, " ").trim().slice(0, 120);
  const have = fold(label);
  const want = fold(args.cible);
  if (!have || !want || !(have.includes(want) || want.includes(have))) {
    return {
      ok: false,
      text: `L'élément n° ${args.ref} est « ${label} », pas « ${args.cible} » : rien n'a été fait.`,
    };
  }
  const autocomplete = node.getAttribute("autocomplete") || "";
  if (kind === "remplir" && (node.type === "password" || /^cc-|password/.test(autocomplete))) {
    return { ok: false, text: "Je ne remplis jamais un mot de passe ni une carte bancaire." };
  }
  if ((risky.test(label) || risky.test(args.valeur ?? "")) && level < 3) {
    return {
      ok: false,
      text: "Action sensible (paiement, achat, suppression) : elle doit être validée exprès.",
    };
  }
  node.scrollIntoView({ block: "center" });
  await pause(150);
  const short = label.length > 40 ? `${label.slice(0, 40)}…` : label;
  await showGesture(
    node.getBoundingClientRect(),
    kind === "cliquer" ? `Clic sur « ${short} »` : `J'écris dans « ${short} »`,
  );
  if (kind === "cliquer") {
    node.click();
    return { ok: true, text: `Cliqué sur « ${label} ».` };
  }
  node.focus();
  if (node.isContentEditable) {
    await typeInto((part) => (node.textContent = part), args.valeur);
  } else if (node instanceof HTMLSelectElement) {
    const option = [...node.options].find((o) => fold(o.text) === fold(args.valeur));
    if (!option) return { ok: false, text: `Pas de choix « ${args.valeur} » dans « ${label} ».` };
    node.value = option.value;
  } else {
    const prototype =
      node instanceof HTMLTextAreaElement
        ? HTMLTextAreaElement.prototype
        : HTMLInputElement.prototype;
    const setValue = Object.getOwnPropertyDescriptor(prototype, "value").set;
    await typeInto((part) => setValue.call(node, part), args.valeur);
  }
  node.dispatchEvent(new Event("input", { bubbles: true }));
  node.dispatchEvent(new Event("change", { bubbles: true }));
  if (args.envoyer) {
    if (node.form) node.form.requestSubmit();
    else node.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
  }
  return { ok: true, text: `Écrit dans « ${label} »${args.envoyer ? ", puis validé" : ""}.` };
}

/** Exécuté dans la page : un clic à un point (en pixels CSS), pour un élément repéré sur une
 *  capture (secours visuel). Refuse une cible sensible non validée au niveau 3. */
function clickAtPoint(action) {
  const risky =
    /pa(?:y|yer|iement)|achet|achat|command|checkout|\bbuy|order|purchase|abonn|subscri|supprim|effac|delete|remove|résili|virement|transf|mot de passe|password|carte|card|cvv|cvc|iban|bancaire/i;
  const { x, y, cible } = action.args;
  const node = document.elementFromPoint(x, y);
  if (!node) return { ok: false, text: "Rien à cet endroit de la page." };
  const target =
    node.closest("a, button, input, select, textarea, [role], [tabindex], [onclick]") ?? node;
  const label = (
    target.getAttribute("aria-label") ||
    target.innerText ||
    target.getAttribute("title") ||
    target.querySelector?.("img[alt]")?.alt ||
    ""
  )
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 120);
  if ((risky.test(label) || risky.test(cible ?? "")) && action.level < 3) {
    return {
      ok: false,
      text: "Action sensible (paiement, achat, suppression) : elle doit être validée exprès.",
    };
  }
  const base = { bubbles: true, cancelable: true, clientX: x, clientY: y, view: window };
  for (const type of ["pointerdown", "mousedown", "pointerup", "mouseup", "click"]) {
    const make = type.startsWith("pointer") ? PointerEvent : MouseEvent;
    target.dispatchEvent(new make(type, { ...base, pointerType: "mouse", button: 0 }));
  }
  return { ok: true, text: `Cliqué au point repéré, sur « ${label || cible} ».` };
}

/** La capture de l'onglet (JPEG) et la taille de sa page, pour le secours visuel. */
async function capture(tabId) {
  const tab = await chrome.tabs.update(tabId, { active: true });
  await new Promise((resolve) => setTimeout(resolve, 300));
  const [size] = await chrome.scripting.executeScript({
    target: { tabId },
    func: () => ({ width: innerWidth, height: innerHeight }),
  });
  let url;
  try {
    url = await chrome.tabs.captureVisibleTab(tab.windowId, { format: "jpeg", quality: 85 });
  } catch {
    // Chrome n'autorise la capture qu'après un clic sur l'icône de l'extension (activeTab).
    return {
      ok: false,
      text: "Chrome refuse la capture : clique une fois sur l'icône d'O-jarvis, sur cet onglet, puis redemande.",
    };
  }
  return {
    ok: true,
    text: "Capture de l'onglet faite.",
    image: url.slice(url.indexOf(",") + 1),
    ...size.result,
  };
}

/** Attend que l'onglet ait fini de charger (après un clic qui change de page), 15 s au plus. */
function settled(tabId) {
  return new Promise((resolve) => {
    const done = () => {
      chrome.tabs.onUpdated.removeListener(listen);
      clearTimeout(timer);
      resolve();
    };
    const listen = (id, change) => {
      if (id === tabId && change.status === "complete") done();
    };
    const timer = setTimeout(done, 15000);
    chrome.tabs.onUpdated.addListener(listen);
    // Rien ne charge : l'action est déjà visible.
    setTimeout(async () => {
      const tab = await chrome.tabs.get(tabId).catch(() => null);
      if (!tab || tab.status === "complete") done();
    }, 1200);
  });
}

async function after(tabId, text) {
  await settled(tabId);
  const tab = await chrome.tabs.get(tabId).catch(() => null);
  return tab ? `${text} L'onglet affiche « ${tab.title} » (${tab.url}).` : text;
}

/** Fait l'action dans l'onglet qu'elle désigne (`args.onglet`) ; rend { ok, text, data? }. */
export async function perform(action) {
  if (action.kind === "lister") {
    const tabs = await chrome.tabs.query({});
    const listed = tabs.map((tab) => ({
      id: tab.id,
      title: tab.title ?? "",
      url: tab.url ?? "",
      active: tab.active,
    }));
    return { ok: true, text: `${listed.length} onglets ouverts.`, data: { tabs: listed } };
  }
  const tabId = Number.isInteger(action.args?.onglet) ? action.args.onglet : null;
  if (tabId === null)
    return { ok: false, text: "Quel onglet ? Liste-les d'abord (chrome_onglets)." };
  try {
    if (action.kind === "lire") {
      const page = await readTab(tabId);
      return page
        ? { ok: true, text: `Onglet « ${page.title} » lu.`, data: { page } }
        : { ok: false, text: "Cet onglet n'a rien rendu." };
    }
    if (action.kind === "ouvrir") {
      await chrome.tabs.update(tabId, { url: action.args.url });
      return { ok: true, text: await after(tabId, `Ouvert : ${action.args.url}.`) };
    }
    if (action.kind === "capturer") return await capture(tabId);
    if (action.kind === "fermer") {
      const tab = await chrome.tabs.get(tabId);
      await chrome.tabs.remove(tabId);
      return { ok: true, text: `Onglet « ${tab.title} » fermé.` };
    }
    const [result] = await chrome.scripting.executeScript({
      target: { tabId },
      func: action.kind === "cliquer_point" ? clickAtPoint : performInPage,
      args: [action],
    });
    const outcome = result?.result ?? { ok: false, text: "L'onglet n'a rien répondu." };
    return outcome.ok ? { ok: true, text: await after(tabId, outcome.text) } : outcome;
  } catch (error) {
    return {
      ok: false,
      text:
        `Je n'ai pas accès à cet onglet (${error.message}). Ouvre le panneau d'O-jarvis sur ` +
        "ce site et autorise-le une fois.",
    };
  }
}
