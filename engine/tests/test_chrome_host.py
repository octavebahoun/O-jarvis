"""L'hôte de messagerie native : messages de Chrome lus et écrits, demandes relayées au moteur."""

from __future__ import annotations

import io
import json
import struct
from typing import Any

import httpx
import pytest

from ojarvis.chrome.host import Host
from ojarvis.chrome.native import MAX_TO_CHROME, NativeMessageError, encode, read_message


def messages(out: io.BytesIO) -> list[dict[str, Any]]:
    stream = io.BytesIO(out.getvalue())
    found = []
    while (message := read_message(stream)) is not None:
        found.append(message)
    return found


def test_messages_are_framed_by_their_length() -> None:
    stream = io.BytesIO(encode({"type": "chat", "text": "Résume"}) + encode({"id": 2}))
    assert read_message(stream) == {"type": "chat", "text": "Résume"}
    assert read_message(stream) == {"id": 2}
    assert read_message(stream) is None  # Chrome a fermé le panneau
    with pytest.raises(NativeMessageError):
        read_message(io.BytesIO(struct.pack("=I", 2) + b"[]"))
    with pytest.raises(NativeMessageError):
        encode({"texte": "x" * MAX_TO_CHROME})


def engine(requests: list[tuple[str, str, Any]]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        requests.append((request.method, request.url.path, body))
        if request.url.path == "/v1/chat":
            if body and body["text"] == "erreur":
                return httpx.Response(422, json={"error": {"message": "Texte refusé."}})
            stream = (
                'event: start\ndata: {"conversation_id": "c1"}\n\n'
                'event: tool\ndata: {"name": "lire_page", "output": "' + "x" * 30_000 + '"}\n\n'
                'event: delta\ndata: {"text": "La page parle de prix."}\n\n'
                'event: done\ndata: {"conversation_id": "c1"}\n\n'
            )
            return httpx.Response(200, text=stream)
        if request.url.path == "/v1/plans/p1/approve":
            return httpx.Response(200, json={"id": "p1", "status": "approved"})
        return httpx.Response(404, json={"error": {"message": "inconnu"}})

    return httpx.Client(base_url="http://moteur", transport=httpx.MockTransport(handler))


def test_a_chat_is_relayed_event_by_event_with_the_tab() -> None:
    requests: list[tuple[str, str, Any]] = []
    out = io.BytesIO()
    page = {"url": "https://exemple.fr", "title": "Exemple", "text": "Prix : 12 €"}
    Host(engine(requests), out).handle({"id": 7, "type": "chat", "text": "Résume.", "page": page})
    assert requests == [
        ("POST", "/v1/chat", {"text": "Résume.", "channel": "chrome", "page": page})
    ]
    sent = messages(out)
    assert [m.get("event", m["type"]) for m in sent] == ["start", "tool", "delta", "done", "end"]
    assert all(m["id"] == 7 for m in sent)
    assert len(sent[1]["data"]["output"]) == 20_000  # une sortie d'outil reste sous 1 Mo
    assert sent[2]["data"] == {"text": "La page parle de prix."}


def test_plans_and_errors_go_back_to_the_panel() -> None:
    requests: list[tuple[str, str, Any]] = []
    out = io.BytesIO()
    host = Host(engine(requests), out)
    host.handle({"id": 1, "type": "approve", "plan_id": "p1", "double": True})
    host.handle({"id": 2, "type": "approve", "plan_id": "../secrets"})
    host.handle({"id": 3, "type": "supprimer_tout"})
    host.handle({"id": 4, "type": "chat", "text": "erreur"})
    assert requests[0] == ("POST", "/v1/plans/p1/approve", {"double_confirm": True})
    assert len(requests) == 2  # ni le faux identifiant ni la demande inconnue n'arrivent
    sent = messages(out)
    assert sent[0] == {"id": 1, "type": "result", "data": {"id": "p1", "status": "approved"}}
    assert sent[1] == {"id": 2, "type": "error", "message": "Plan inconnu."}
    assert sent[2] == {"id": 3, "type": "error", "message": "Demande inconnue."}
    assert sent[3] == {"id": 4, "type": "error", "message": "Texte refusé."}
