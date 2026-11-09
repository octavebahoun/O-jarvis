// Le fond de l'extension, actif tant que Chrome tourne : il garde le lien avec Jarvis, même
// panneau fermé, et fait dans tes onglets les actions validées, d'où que vienne la demande
// (panneau, terminal, app ou voix). Un lien natif ouvert garde ce fond éveillé.
import { perform } from "./actions.js";

const HOST = "io.github.octavebahoun.ojarvis";
/** Après une coupure de l'hôte (moteur relancé, mise à jour), on se rebranche. */
const RETRY_MS = 10000;

// Un clic sur l'icône d'O-jarvis ouvre son panneau, à côté de la page.
chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch(() => undefined);

function connect() {
  const port = chrome.runtime.connectNative(HOST);
  port.postMessage({ type: "hello", role: "background" });
  port.onMessage.addListener(async (message) => {
    if (message.type !== "action") return;
    const result = await perform(message.action);
    port.postMessage({ type: "action_result", id: 0, action_id: message.action.id, ...result });
  });
  port.onDisconnect.addListener(() => setTimeout(connect, RETRY_MS));
}

connect();
