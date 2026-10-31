// L'onglet ouvert : son titre et son adresse, et, sur les sites que tu autorises, son texte
// et ses éléments numérotés (liens, boutons, champs) pour que Jarvis puisse y agir.

/** Exécuté dans la page : le texte visible et les éléments utiles, jamais le contenu des
 *  champs, ni les mots de passe, ni les cookies. */
function readPage() {
  const labelOf = (node) => {
    const aria = node.getAttribute("aria-label");
    if (aria) return aria;
    if (node.labels?.length) return [...node.labels].map((label) => label.innerText).join(" ");
    if (node.tagName === "INPUT" && ["submit", "button", "reset"].includes(node.type)) {
      return node.value;
    }
    if (node.innerText?.trim()) return node.innerText;
    const image = node.querySelector?.("img[alt]");
    return (
      node.getAttribute("placeholder") ||
      node.getAttribute("title") ||
      image?.alt ||
      node.getAttribute("name") ||
      ""
    );
  };
  for (const old of document.querySelectorAll("[data-ojarvis-ref]")) {
    old.removeAttribute("data-ojarvis-ref");
  }
  // Les applications web (Discord, Gmail…) déclarent leurs éléments cliquables par un rôle :
  // élément d'arbre, onglet, entrée de menu, option.
  const roles = [
    "button",
    "link",
    "checkbox",
    "radio",
    "switch",
    "tab",
    "treeitem",
    "menuitem",
    "menuitemcheckbox",
    "menuitemradio",
    "option",
  ];
  const selector = [
    "a[href]",
    "button",
    'input:not([type="hidden"]):not([type="password"])',
    "textarea",
    "select",
    '[contenteditable="true"]',
    ...roles.map((role) => `[role="${role}"]`),
  ].join(", ");
  const elements = [];
  for (const node of document.querySelectorAll(selector)) {
    if (elements.length >= 300) break;
    const box = node.getBoundingClientRect();
    const style = getComputedStyle(node);
    if ((!box.width && !box.height) || style.visibility === "hidden") continue;
    const tag = node.tagName.toLowerCase();
    const type = (node.getAttribute("type") || "").toLowerCase();
    const role = node.getAttribute("role");
    let kind = "bouton";
    if (tag === "a" || role === "link") kind = "lien";
    else if (tag === "select") kind = "liste";
    else if (
      ["checkbox", "radio"].includes(type) ||
      ["checkbox", "radio", "switch"].includes(role)
    ) {
      kind = "case";
    } else if (
      tag === "textarea" ||
      node.isContentEditable ||
      (tag === "input" && !["submit", "button", "reset", "image"].includes(type))
    ) {
      kind = "champ";
    }
    const label = labelOf(node).replace(/\s+/g, " ").trim().slice(0, 120);
    if (!label && kind !== "champ") continue;
    const ref = elements.length + 1;
    node.setAttribute("data-ojarvis-ref", String(ref));
    const href = kind === "lien" && /^https?:/.test(node.href ?? "") ? node.href : "";
    elements.push({ ref, kind, label: label || "(sans nom)", href: href.slice(0, 2000) });
  }
  return {
    url: location.href,
    title: document.title,
    text: (document.body?.innerText ?? "").slice(0, 50000),
    selection: String(getSelection() ?? "").slice(0, 4000),
    elements,
  };
}

export async function activeTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab ?? null;
}

/** L'origine d'une adresse web (http ou https) ; null pour une page interne de Chrome. */
export function originOf(url) {
  try {
    const parsed = new URL(url);
    return ["http:", "https:"].includes(parsed.protocol) ? parsed.origin : null;
  } catch {
    return null;
  }
}

/** Demande l'accès au site, une fois par site : Chrome ne l'accepte que pendant un clic. */
export function allowSite(origin) {
  return chrome.permissions.request({ origins: [`${origin}/*`] });
}

export async function readTab(tabId) {
  const [result] = await chrome.scripting.executeScript({ target: { tabId }, func: readPage });
  return result?.result ?? null;
}
