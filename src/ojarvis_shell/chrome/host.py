"""L'hôte de messagerie native : le pont entre l'extension Chrome et le moteur.

Chrome le lance quand le panneau s'ouvre (`connectNative`) et ferme son entrée quand le
panneau se ferme. Il rejoint le moteur de l'app, ou en lance un comme le terminal, puis
relaie les demandes du panneau vers l'API et le flux de la réponse vers le panneau. Sa sortie
standard ne porte que des messages : tout le reste va sur la sortie d'erreur.
"""

from __future__ import annotations

import contextlib
import os
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, BinaryIO

import httpx

from ojarvis import __version__
from ojarvis.chrome.native import NativeMessageError, encode, read_message
from ojarvis.shell.connection import EngineUnavailableError, connect, stop_engine
from ojarvis.shell.display import error_message, sse_events

#: Identifiant d'un plan dans un chemin d'API : rien d'autre ne passe.
PLAN_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
#: Ce que le panneau peut faire d'un plan : (méthode, fin du chemin).
PLAN_ACTIONS = {
    "plan": ("GET", ""),
    "approve": ("POST", "/approve"),
    "refuse": ("POST", "/refuse"),
    "stop": ("POST", "/stop"),
}
#: Sortie d'un outil gardée dans un événement (un message vers Chrome fait 1 Mo au plus).
MAX_OUTPUT_CHARS = 20_000
UNREACHABLE = "Le moteur de Jarvis ne répond pas."


class Host:
    """Traite les demandes du panneau ; chaque réponse reprend l'`id` de sa demande."""

    def __init__(self, http: httpx.Client, out: BinaryIO) -> None:
        self.http = http
        self._out = out
        self._lock = threading.Lock()

    def send(self, message: dict[str, Any]) -> None:
        with self._lock, contextlib.suppress(OSError):  # le panneau a pu se fermer
            self._out.write(encode(message))
            self._out.flush()

    def handle(self, message: dict[str, Any]) -> None:
        ident = message.get("id")
        kind = message.get("type")
        try:
            if kind == "chat":
                self._chat(ident, message)
            elif isinstance(kind, str) and kind in PLAN_ACTIONS:
                self._plan(ident, kind, message)
            else:
                self.send({"id": ident, "type": "error", "message": "Demande inconnue."})
        except httpx.HTTPError:
            self.send({"id": ident, "type": "error", "message": UNREACHABLE})
        except NativeMessageError as error:
            self.send({"id": ident, "type": "error", "message": f"Réponse trop grande : {error}."})

    def _chat(self, ident: Any, message: dict[str, Any]) -> None:
        body: dict[str, Any] = {"text": str(message.get("text", "")), "channel": "chrome"}
        if message.get("conversation_id"):
            body["conversation_id"] = str(message["conversation_id"])
        if isinstance(message.get("page"), dict):
            body["page"] = message["page"]
        with self.http.stream("POST", "/v1/chat", json=body, timeout=None) as response:
            if response.status_code != 200:
                response.read()
                self.send({"id": ident, "type": "error", "message": error_message(response)})
                return
            for kind, data in sse_events(response.iter_lines()):
                if isinstance(data.get("output"), str):
                    data["output"] = data["output"][-MAX_OUTPUT_CHARS:]
                self.send({"id": ident, "type": "event", "event": kind, "data": data})
        self.send({"id": ident, "type": "end"})

    def _plan(self, ident: Any, kind: str, message: dict[str, Any]) -> None:
        plan_id = str(message.get("plan_id", ""))
        if not PLAN_ID.fullmatch(plan_id):
            self.send({"id": ident, "type": "error", "message": "Plan inconnu."})
            return
        method, action = PLAN_ACTIONS[kind]
        body = {"double_confirm": bool(message.get("double"))} if kind == "approve" else None
        response = self.http.request(method, f"/v1/plans/{plan_id}{action}", json=body)
        if response.status_code >= 400:
            self.send({"id": ident, "type": "error", "message": error_message(response)})
            return
        data = response.json() if response.content else {}
        self.send({"id": ident, "type": "result", "data": data})


def main(argv: list[str] | None = None) -> int:
    """Lancé par Chrome, avec pour argument l'origine de l'extension (déjà filtrée par le
    manifeste de l'hôte, qui n'autorise que la nôtre)."""
    del argv
    channel_in, channel_out = sys.stdin.buffer, sys.stdout.buffer
    # Une impression égarée casserait les messages : la sortie standard Python part ailleurs.
    sys.stdout = sys.stderr
    try:
        info, process = connect(os.environ)
    except EngineUnavailableError as error:
        channel_out.write(encode({"type": "error", "message": str(error)}))
        channel_out.flush()
        return 1
    headers = {"Authorization": f"Bearer {info.token}"}
    try:
        with (
            httpx.Client(base_url=info.url, headers=headers, timeout=30.0) as http,
            ThreadPoolExecutor(max_workers=4) as pool,
        ):
            host = Host(http, channel_out)
            host.send({"type": "ready", "version": __version__, "own_engine": process is not None})
            while (message := read_message(channel_in)) is not None:
                pool.submit(host.handle, message)
    except NativeMessageError:
        return 1
    finally:
        stop_engine(process)
    return 0
