// L'onglet ouvert : son titre et son adresse, et, sur les sites que tu autorises, son texte.

/** Exécuté dans la page : le texte visible, jamais les champs de saisie ni les cookies. */
function readPage() {
  return {
    url: location.href,
    title: document.title,
    text: (document.body?.innerText ?? "").slice(0, 50000),
    selection: String(getSelection() ?? "").slice(0, 4000),
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

export async function readTab(tab) {
  const [result] = await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    func: readPage,
  });
  return result?.result ?? null;
}
