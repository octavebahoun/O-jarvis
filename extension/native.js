// La messagerie native vers Jarvis (voir engine/src/ojarvis/chrome/host.py) : chaque demande
// porte un identifiant, et toutes ses réponses le reprennent.
const HOST = "io.github.octavebahoun.ojarvis";

export class Jarvis {
  constructor({ onReady, onFatal, onClosed }) {
    this.next = 1;
    this.pending = new Map();
    this.port = chrome.runtime.connectNative(HOST);
    this.port.postMessage({ type: "hello", role: "panel" });
    this.port.onMessage.addListener((message) => {
      if (message.type === "ready") onReady(message);
      else if (message.id === undefined && message.type === "error") onFatal(message.message);
      else this.#receive(message);
    });
    this.port.onDisconnect.addListener(() => {
      const reason = chrome.runtime.lastError?.message ?? "";
      for (const handlers of this.pending.values()) handlers.onError?.("Jarvis s'est arrêté.");
      this.pending.clear();
      onClosed(reason);
    });
  }

  #receive(message) {
    const handlers = this.pending.get(message.id);
    if (!handlers) return;
    if (message.type === "event") {
      handlers.onEvent?.(message.event, message.data);
      return;
    }
    this.pending.delete(message.id);
    if (message.type === "end") handlers.onEnd?.();
    else if (message.type === "result") handlers.onResult?.(message.data);
    else handlers.onError?.(message.message ?? "Réponse inattendue de Jarvis.");
  }

  /** Une demande à plusieurs réponses (une conversation, événement par événement). */
  request(message, handlers) {
    const id = this.next++;
    this.pending.set(id, handlers);
    this.port.postMessage({ ...message, id });
  }

  /** Une demande à réponse unique (un plan), sous forme de promesse. */
  ask(message) {
    return new Promise((resolve, reject) => {
      this.request(message, {
        onResult: resolve,
        onError: (text) => reject(new Error(text)),
      });
    });
  }
}
