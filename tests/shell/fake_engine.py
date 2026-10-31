"""Un faux moteur pour les tests du terminal : il rejoue des tours de chat, suit un plan et
garde chaque requête reçue."""

from __future__ import annotations

import io
import json
from collections.abc import Iterator
from typing import Any

import httpx

from ojarvis.shell.cli import ShellClient
from ojarvis.shell.display import Style


def sse(*events: tuple[str, dict[str, Any]]) -> str:
    return "".join(f"event: {kind}\ndata: {json.dumps(data)}\n\n" for kind, data in events)


START = ("start", {"conversation_id": "c1", "user_message_id": 1, "assistant_message_id": 2})


ROUTE = ("route", {"provider": "meta", "model": "muse-spark-1.3", "fallback_from": None})


DONE: tuple[str, dict[str, Any]] = ("done", {})


def plan(level: int, status: str = "proposed") -> dict[str, Any]:
    return {
        "id": "p1",
        "title": "Nettoyer",
        "status": status,
        "max_level": level,
        "tainted": False,
        "steps": [{"position": 1, "description": "Lancer « docker image prune -f »", "level": 3}],
    }


class FakeEngine:
    """Rejoue des tours de chat et suit un plan ; garde chaque requête reçue."""

    def __init__(self, turns: list[str]) -> None:
        self.turns: Iterator[str] = iter(turns)
        self.requests: list[tuple[str, str, dict[str, Any]]] = []
        self.plan_polls = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        self.requests.append((request.method, request.url.path, body))
        if request.url.path == "/v1/chat":
            return httpx.Response(200, text=next(self.turns))
        if request.url.path.endswith(("/approve", "/refuse")):
            return httpx.Response(200, json={})
        if request.url.path == "/v1/plans/p1":
            self.plan_polls += 1
            status = "running" if self.plan_polls == 1 else "done"
            step = {
                "position": 1,
                "tool": "executer_commande",
                "description": "Lancer « docker image prune -f »",
                "status": "running" if status == "running" else "done",
                "result": "$ docker image prune -f\ndossier : ~\ncode de sortie 0 (1 s)\n"
                "--- sortie ---\nTotal reclaimed space: 25.4GB",
                "error": None,
            }
            return httpx.Response(200, json={"status": status, "steps": [step]})
        return httpx.Response(404, json={"error": {"message": "inconnu"}})


def session(
    engine: FakeEngine, answers: list[str], style: Style | None = None
) -> tuple[ShellClient, io.StringIO]:
    out = io.StringIO()
    replies = iter(answers)
    http = httpx.Client(base_url="http://moteur", transport=httpx.MockTransport(engine))
    client = ShellClient(
        http,
        "/home/test/projet",
        out=out,
        ask=lambda _q: next(replies),
        style=style or Style(False),
    )
    client._poll_s = 0.0
    return client, out


def chats(engine: FakeEngine) -> list[dict[str, Any]]:
    return [body for method, path, body in engine.requests if path == "/v1/chat"]
