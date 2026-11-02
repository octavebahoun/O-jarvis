"""Mémoire graphe : personnes, projets, événements reliés ; remplie par l'extraction,
modifiable par toi, utilisée dans le contexte quand tu en parles."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from ojarvis.llm.providers.fake import FakeProvider, FakeReply
from ojarvis.llm.structured import TOOL_NAME
from ojarvis.llm.types import LLMRequest, ToolCall
from ojarvis.memory.graph import name_key
from ojarvis.services import Services
from ojarvis.testing.chat import read_sse

LEARNED: dict[str, Any] = {
    "faits": [{"type": "fait", "contenu": "L'utilisateur construit O-jarvis.", "confiance": 0.9}],
    "entites": [
        {"nom": "moi", "type": "moi"},
        {"nom": "O-jarvis", "type": "projet", "description": "Assistant personnel desktop."},
        {"nom": "Aïcha", "type": "personne", "description": "Sa sœur."},
        {"nom": "Cotonou", "type": "lieu"},
    ],
    "relations": [
        {"source": "moi", "lien": "travaille sur", "cible": "O-jarvis"},
        {"source": "moi", "lien": "habite à", "cible": "Cotonou"},
        {"source": "Aïcha", "lien": "teste", "cible": "O-jarvis"},
        {"source": "moi", "lien": "connaît", "cible": "Inconnu"},  # cible absente : ignorée
    ],
}


def learning_script(extraction: dict[str, Any]) -> Any:
    def script(request: LLMRequest) -> FakeReply:
        if request.tools and request.tools[0].name == TOOL_NAME:
            return FakeReply(tool_calls=[ToolCall("m", TOOL_NAME, extraction)])
        if "titre" in request.messages[0].content:
            return FakeReply(text="Projet")
        return FakeReply(text="Noté.")

    return script


def test_names_are_compared_without_case_or_accents() -> None:
    assert name_key("  Aïcha ") == name_key("aicha") == "aicha"
    assert name_key("O-Jarvis") == "o-jarvis"


def test_a_conversation_fills_the_graph(
    client: TestClient, services: Services, llm: dict[str, FakeProvider]
) -> None:
    llm["deepseek"]._script = learning_script(LEARNED)
    read_sse(
        client.post(
            "/v1/chat", json={"text": "Je construis O-jarvis à Cotonou ; Aïcha, ma sœur, le teste."}
        ).text
    )
    assert client.portal is not None
    client.portal.call(services.chat.drain)
    graph = client.get("/v1/memory/graph").json()
    names = {e["name"]: e for e in graph["entities"]}
    assert set(names) == {"Moi", "O-jarvis", "Aïcha", "Cotonou"}
    assert names["O-jarvis"]["description"] == "Assistant personnel desktop."
    by_id = {e["id"]: e["name"] for e in graph["entities"]}
    links = {(by_id[r["source_id"]], r["label"], by_id[r["target_id"]]) for r in graph["relations"]}
    assert links == {
        ("Moi", "travaille sur", "O-jarvis"),
        ("Moi", "habite à", "Cotonou"),
        ("Aïcha", "teste", "O-jarvis"),
    }

    # Revu dans une autre conversation (écrit autrement) : même entité, une mention de plus.
    llm["deepseek"]._script = learning_script(
        {
            "entites": [{"nom": "aicha", "type": "personne"}, {"nom": "moi", "type": "moi"}],
            "relations": [{"source": "Aicha", "lien": "teste", "cible": "o-jarvis"}],
        }
    )
    read_sse(client.post("/v1/chat", json={"text": "Aicha a trouvé un bug."}).text)
    client.portal.call(services.chat.drain)
    again = client.get("/v1/memory/graph").json()
    assert len(again["entities"]) == 4
    aicha = next(e for e in again["entities"] if e["name"] == "Aïcha")
    assert aicha["mentions"] == 2

    # Quand tu en parles, ses liens entrent dans le contexte.
    llm["deepseek"]._script = learning_script({})
    read_sse(client.post("/v1/chat", json={"text": "Où en est O-jarvis ?"}).text)
    system = [
        r.messages[0].content
        for r, _ in llm["deepseek"].requests
        if r.messages and "<memoire>" in r.messages[0].content
    ][-1]
    assert "[graphe] O-jarvis (projet) — Assistant personnel desktop." in system
    assert "Moi travaille sur O-jarvis" in system


def test_you_can_correct_complete_and_erase_the_graph(client: TestClient) -> None:
    project = client.post(
        "/v1/memory/graph/entities", json={"name": "Thèse", "kind": "projet"}
    ).json()
    person = client.post(
        "/v1/memory/graph/entities",
        json={"name": "Pr Adjovi", "kind": "personne", "description": "Directrice."},
    ).json()
    duplicate = client.post("/v1/memory/graph/entities", json={"name": "these", "kind": "sujet"})
    assert duplicate.status_code == 409
    link = client.post(
        "/v1/memory/graph/relations",
        json={"source_id": person["id"], "label": "dirige", "target_id": project["id"]},
    ).json()
    assert link["label"] == "dirige"
    renamed = client.patch(
        f"/v1/memory/graph/entities/{project['id']}",
        json={"name": "Thèse de master", "description": "Sur la voix."},
    ).json()
    assert (renamed["name"], renamed["description"]) == ("Thèse de master", "Sur la voix.")
    assert client.delete(f"/v1/memory/graph/relations/{link['id']}").status_code == 204
    client.post(
        "/v1/memory/graph/relations",
        json={"source_id": person["id"], "label": "dirige", "target_id": project["id"]},
    )
    # Effacer une entité efface ses liens.
    assert client.delete(f"/v1/memory/graph/entities/{project['id']}").status_code == 204
    graph = client.get("/v1/memory/graph").json()
    assert [e["name"] for e in graph["entities"]] == ["Pr Adjovi"]
    assert graph["relations"] == []
    events = [e["type"] for e in client.get("/v1/events", params={"limit": 20}).json()]
    assert "memory.graph" in events
