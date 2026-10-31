// Les actions validées, faites dans ton onglet : cliquer, remplir, ouvrir. Chacune vérifie
// que l'élément est bien celui du plan, et refuse un mot de passe, une carte bancaire, ou une
// action sensible (paiement, achat, suppression) qui n'a pas été validée au niveau 3.

/** Exécuté dans la page, avec l'action en argument (rien d'autre n'y est disponible). */
function performInPage(action) {
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
  if (kind === "cliquer") {
    node.click();
    return { ok: true, text: `Cliqué sur « ${label} ».` };
  }
  node.focus();
  if (node.isContentEditable) {
    node.textContent = args.valeur;
  } else if (node instanceof HTMLSelectElement) {
    const option = [...node.options].find((o) => fold(o.text) === fold(args.valeur));
    if (!option) return { ok: false, text: `Pas de choix « ${args.valeur} » dans « ${label} ».` };
    node.value = option.value;
  } else {
    const prototype =
      node instanceof HTMLTextAreaElement
        ? HTMLTextAreaElement.prototype
        : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(prototype, "value").set.call(node, args.valeur);
  }
  node.dispatchEvent(new Event("input", { bubbles: true }));
  node.dispatchEvent(new Event("change", { bubbles: true }));
  if (args.envoyer) {
    if (node.form) node.form.requestSubmit();
    else node.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
  }
  return { ok: true, text: `Écrit dans « ${label} »${args.envoyer ? ", puis validé" : ""}.` };
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

/** Fait l'action dans l'onglet `tabId` ; rend { ok, text } pour Jarvis. */
export async function perform(tabId, action) {
  if (tabId === null) {
    return {
      ok: false,
      text: "Je n'ai lu aucun onglet : demande-moi depuis l'onglet à manipuler.",
    };
  }
  try {
    if (action.kind === "ouvrir") {
      await chrome.tabs.update(tabId, { url: action.args.url });
      return { ok: true, text: await after(tabId, `Ouvert : ${action.args.url}.`) };
    }
    const [result] = await chrome.scripting.executeScript({
      target: { tabId },
      func: performInPage,
      args: [action],
    });
    const outcome = result?.result ?? { ok: false, text: "L'onglet n'a rien répondu." };
    return outcome.ok ? { ok: true, text: await after(tabId, outcome.text) } : outcome;
  } catch (error) {
    return {
      ok: false,
      text: `Je ne peux pas agir dans cet onglet (${error.message}). Autorise ce site depuis le panneau.`,
    };
  }
}
