"""L'hôte de messagerie native : le pont entre l'extension Chrome et le moteur.

Chrome en lance un par connexion de l'extension, et ferme son entrée quand elle se ferme.
Le premier message dit qui parle :
- le fond de l'extension, branché tant que Chrome tourne : il rejoint le moteur dès qu'il en
  trouve un (sans jamais en lancer) et lui fait faire les actions dans tes onglets ;
- un panneau : il rejoint le moteur de l'app, ou en lance un comme le terminal, et relaie la
  conversation (demandes vers l'API, flux de la réponse vers le panneau).
Sa sortie standard ne porte que des messages : tout le reste va sur la sortie d'erreur.
"""

from __future__ import annotations

import contextlib
import os
import re
import sys
import threading
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from typing import Any, BinaryIO

import httpx

from ojarvis import __version__
from ojarvis.chrome.native import NativeMessageError, encode, read_message
from ojarvis.config import resolve_paths
from ojarvis.shell.connection import (
    EngineInfo,
    EngineLink,
    EngineUnavailableError,
    info_path,
    reachable,
    read_info,
)
from ojarvis.shell.display import error_message, sse_events

#: Identifiant d'un plan ou d'une action dans un chemin d'API : rien d'autre ne passe.
PLAN_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
#: Attente d'une action à faire dans l'onglet, par requête longue (secondes).
ACTION_WAIT_S = 20
#: Le fond de l'extension cherche un moteur à rejoindre tous les… (secondes).
ENGINE_RETRY_S = 5.0
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
            elif kind == "action_result":
                self._action_result(ident, message)
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
        # La suite d'un plan fait dans l'onglet : Jarvis en lit les résultats et continue.
        after = message.get("after_plan")
        if isinstance(after, list) and all(
            isinstance(a, str) and PLAN_ID.fullmatch(a) for a in after
        ):
            body["after_plan"] = after
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

    def _action_result(self, ident: Any, message: dict[str, Any]) -> None:
        action_id = str(message.get("action_id", ""))
        if not PLAN_ID.fullmatch(action_id):
            self.send({"id": ident, "type": "error", "message": "Action inconnue."})
            return
        body: dict[str, Any] = {
            "ok": bool(message.get("ok")),
            "text": str(message.get("text", ""))[:20_000],
        }
        # Une capture de l'onglet (secours visuel) : l'image et la taille de la page.
        if isinstance(message.get("data"), dict):
            body["data"] = message["data"]
        if isinstance(message.get("image"), str):
            body |= {
                "image": message["image"],
                "width": int(message.get("width") or 0),
                "height": int(message.get("height") or 0),
            }
        response = self.http.post(f"/v1/chrome/actions/{action_id}", json=body)
        if response.status_code >= 400:
            self.send({"id": ident, "type": "error", "message": error_message(response)})
            return
        self.send({"id": ident, "type": "result", "data": {}})

    def poll_actions(self, stop: threading.Event) -> None:
        """Les actions validées à faire dans tes onglets : passées à l'extension dès qu'elles
        arrivent. Rend la main quand le moteur ne répond plus (arrêté, ou relancé)."""
        while not stop.is_set():
            try:
                response = self.http.get(
                    "/v1/chrome/actions/next",
                    params={"wait": ACTION_WAIT_S},
                    timeout=ACTION_WAIT_S + 10,
                )
            except httpx.HTTPError:
                return
            if response.status_code == 200:
                self.send({"type": "action", "action": response.json()})
            elif response.status_code == 401:
                return  # un autre moteur a pris la place : on le rejoindra
            elif response.status_code != 204:
                stop.wait(2.0)


def _join(env: Mapping[str, str]) -> EngineInfo | None:
    """Le moteur qui tourne (celui de l'app ou du terminal), sans jamais en lancer un."""
    path = info_path(resolve_paths(env), env)
    info = read_info(path) if path is not None else None
    return info if info is not None and reachable(info) else None


def _background(channel_in: BinaryIO, channel_out: BinaryIO) -> int:
    """Le fond de l'extension, branché tant que Chrome tourne : dès qu'un moteur tourne, il
    le rejoint et lui fait faire les actions dans tes onglets, que la demande vienne du
    panneau, du terminal, de l'app ou de la voix."""
    current: list[Host] = []
    closed = threading.Event()

    def engine_loop() -> None:
        while not closed.is_set():
            info = _join(os.environ)
            if info is None:
                closed.wait(ENGINE_RETRY_S)
                continue
            headers = {"Authorization": f"Bearer {info.token}"}
            with httpx.Client(base_url=info.url, headers=headers, timeout=30.0) as http:
                host = Host(http, channel_out)
                current[:] = [host]
                host.send({"type": "ready", "version": __version__})
                host.poll_actions(closed)
                current.clear()
            closed.wait(ENGINE_RETRY_S)

    with ThreadPoolExecutor(max_workers=4) as pool:
        pool.submit(engine_loop)
        try:
            while (message := read_message(channel_in)) is not None:
                if current:
                    pool.submit(current[0].handle, message)
        except NativeMessageError:
            return 1
        finally:
            closed.set()
    return 0


def _panel(channel_in: BinaryIO, channel_out: BinaryIO, first: dict[str, Any]) -> int:
    """Le panneau : il rejoint le moteur de l'app, ou en lance un comme le terminal."""
    link = EngineLink(os.environ)
    try:
        process = link.open()
    except EngineUnavailableError as error:
        channel_out.write(encode({"type": "error", "message": str(error)}))
        channel_out.flush()
        return 1
    try:
        with link.client(timeout=30.0) as http, ThreadPoolExecutor(max_workers=4) as pool:
            host = Host(http, channel_out)
            host.send({"type": "ready", "version": __version__, "own_engine": process is not None})
            if first.get("type") != "hello":
                pool.submit(host.handle, first)
            while (message := read_message(channel_in)) is not None:
                pool.submit(host.handle, message)
    except NativeMessageError:
        return 1
    finally:
        link.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    """Lancé par Chrome, avec pour argument l'origine de l'extension (déjà filtrée par le
    manifeste de l'hôte, qui n'autorise que la nôtre). Le premier message dit qui parle :
    le fond de l'extension, ou un panneau."""
    del argv
    channel_in, channel_out = sys.stdin.buffer, sys.stdout.buffer
    # Une impression égarée casserait les messages : la sortie standard Python part ailleurs.
    sys.stdout = sys.stderr
    try:
        first = read_message(channel_in)
    except NativeMessageError:
        return 1
    if first is None:
        return 0
    if first.get("type") == "hello" and first.get("role") == "background":
        return _background(channel_in, channel_out)
    return _panel(channel_in, channel_out, first)
