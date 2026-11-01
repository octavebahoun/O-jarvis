"""Apprentissage et anticipation : des suggestions avec un score de confiance, qui ne font
rien seules ; l'anticipation lit la mémoire sans jamais la modifier."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
from sqlalchemy import select, update

from ojarvis.anticipation.predictor import Predictor
from ojarvis.anticipation.repetition import RepetitionWatcher
from ojarvis.anticipation.store import SuggestionStore
from ojarvis.conversations.store import MessageRecord
from ojarvis.db.models import ChatMessage, GraphEntity, GraphRelation
from ojarvis.events.bus import Event
from ojarvis.llm.providers.fake import FakeProvider
from ojarvis.memory.graph import EntityRecord, RelationRecord
from ojarvis.services import Services
from ojarvis.testing.chat import read_sse

PARIS = ZoneInfo("Europe/Paris")
NOW = datetime(2026, 10, 15, 6, 10, tzinfo=UTC)  # 8 h 10 à Paris
REQUEST = "Résume-moi les issues GitHub ouvertes de O-jarvis."


async def ask(services: Services, text: str, when: datetime) -> MessageRecord:
    """Une demande passée, datée : comme si tu l'avais faite ce jour-là."""
    conversation = await services.conversations.create("app")
    message = await services.conversations.add_message(conversation.id, "user", text)

    def backdate(session: Any) -> None:
        session.execute(
            update(ChatMessage).where(ChatMessage.id == message.id).values(created_at=when)
        )

    await services.db.run(backdate)
    return dataclasses.replace(message, created_at=when)


def watcher(services: Services) -> RepetitionWatcher:
    return RepetitionWatcher(
        services.conversations,
        services.anticipation.store,
        embedder_id=services.embedder.model_id,
        timezone=lambda: PARIS,
        clock=lambda: NOW,
    )


def test_same_request_means_same_names() -> None:
    from ojarvis.anticipation.repetition import names  # noqa: PLC0415

    assert names(REQUEST) == {"github", "o-jarvis"}
    assert names("Fais-moi le point sur les issues GitHub de O-jarvis") == {"github", "o-jarvis"}
    assert names("Résume-moi les issues GitHub ouvertes de Notion.") == {"github", "notion"}
    assert names("Météo à Cotonou ?") == names("Quel temps fera-t-il à Cotonou ?") == {"cotonou"}


def test_a_request_made_three_days_running_becomes_a_suggestion(
    client: TestClient, services: Services
) -> None:
    async def scenario() -> list[Any]:
        spotter = watcher(services)
        first = await ask(services, REQUEST, NOW - timedelta(days=2, minutes=15))
        assert await spotter.check(first) is None
        second = await ask(services, REQUEST, NOW - timedelta(days=1, minutes=-20))
        assert await spotter.check(second) is None  # deux jours : pas encore une habitude
        other = await ask(services, "Quelle heure est-il à Tokyo ?", NOW)
        assert await spotter.check(other) is None
        third = await ask(services, REQUEST, NOW)
        made = await spotter.check(third)
        fourth = await ask(services, REQUEST, NOW + timedelta(days=1))
        again = await spotter.check(fourth)  # même raison : pas de doublon
        return [made, again]

    assert client.portal is not None
    made, again = client.portal.call(scenario)
    assert made is not None
    assert again is None
    assert made.title == f"Tu m'as demandé « {REQUEST} » 3 jours différents ce mois-ci."
    assert made.detail == "Souvent vers 8 h 10."
    assert made.confidence == 0.85
    automation, skill = made.options
    assert (
        automation.prompt
        == f"Crée une automatisation : chaque jour à 8 h 10, fais ceci : {REQUEST}"
    )
    assert skill.id == "skill"
    listed = client.get("/v1/suggestions").json()
    assert [(s["kind"], [o["id"] for o in s["options"]]) for s in listed] == [
        ("repetition", ["automatisation", "skill"])
    ]
    assert "prompt" not in listed[0]["options"][0]  # le message part du moteur, pas de l'écran


def test_the_watcher_listens_to_finished_turns_but_not_to_automations(
    client: TestClient, services: Services, llm: dict[str, FakeProvider]
) -> None:
    assert client.portal is not None
    yesterday = datetime.now(UTC) - timedelta(days=1)
    for days in (2, 1):
        past = client.portal.call(ask, services, REQUEST, yesterday - timedelta(days=days - 1))
        client.portal.call(services.conversations.index_request, past)
    # Une demande faite par une automatisation n'est pas une habitude : ignorée.
    automatic = Event(
        type="chat.completed",
        ts=datetime.now(UTC),
        source="chat",
        payload={"conversation_id": "x", "message_id": 1, "channel": "automation"},
    )
    client.portal.call(services.anticipation._watcher.on_completed, automatic)
    assert client.get("/v1/suggestions").json() == []
    read_sse(client.post("/v1/chat", json={"text": REQUEST}).text)
    client.portal.call(services.chat.drain)
    client.portal.call(services.bus.drain)
    assert [s["kind"] for s in client.get("/v1/suggestions").json()] == ["repetition"]


async def _graph_seen(
    services: Services, name: str, kind: str, mentions: int, when: datetime
) -> None:
    await services.graph.remember([(name, kind, "Un projet suivi.")], [])

    def age(session: Any) -> None:
        session.execute(
            update(GraphEntity)
            .where(GraphEntity.name == name)
            .values(mentions=mentions, last_seen_at=when)
        )

    await services.db.run(age)


class ReadOnlyGraph:
    """Ce que reçoit l'anticipation : une lecture, rien d'autre."""

    def __init__(self, services: Services) -> None:
        self._services = services

    async def graph(self, *, limit: int = 300) -> tuple[list[EntityRecord], list[RelationRecord]]:
        return await self._services.graph.graph(limit=limit)


def test_anticipation_notices_a_project_left_aside_and_never_writes_memory(
    client: TestClient, services: Services
) -> None:
    def snapshot(session: Any) -> list[Any]:
        rows = session.scalars(select(GraphEntity)).all()
        links = session.scalars(select(GraphRelation)).all()
        return [(r.id, r.name, r.mentions, r.last_seen_at) for r in rows] + [
            (r.id, r.label, r.mentions) for r in links
        ]

    async def scenario() -> tuple[list[Any], list[Any], list[Any], list[Any]]:
        await _graph_seen(services, "O-jarvis", "projet", 4, NOW - timedelta(days=7))
        await _graph_seen(services, "Thèse", "projet", 5, NOW - timedelta(days=1))  # récent
        await _graph_seen(services, "Cotonou", "lieu", 9, NOW - timedelta(days=30))  # un lieu
        await _graph_seen(services, "Rust", "sujet", 2, NOW - timedelta(days=20))  # peu cité
        before = await services.db.run(snapshot)
        predictor = Predictor(
            ReadOnlyGraph(services), services.anticipation.store, clock=lambda: NOW
        )
        made = await predictor.run()
        twice = await predictor.run()
        after = await services.db.run(snapshot)
        return made, twice, before, after

    assert client.portal is not None
    made, twice, before, after = client.portal.call(scenario)
    assert [s.title for s in made] == ["Tu n'as pas touché à O-jarvis depuis 7 jours."]
    assert made[0].confidence == 0.75
    assert made[0].options[0].label == "Reprendre"
    assert twice == []
    assert before == after  # la mémoire n'a pas bougé


def test_accepting_opens_a_conversation_dismissing_puts_it_away(
    client: TestClient, services: Services, llm: dict[str, FakeProvider]
) -> None:
    store: SuggestionStore = services.anticipation.store
    assert client.portal is not None

    async def two() -> list[str]:
        from ojarvis.anticipation.store import Option  # noqa: PLC0415

        ids = []
        for n in (1, 2):
            made = await store.propose(
                kind="inactivite",
                title=f"Suggestion {n}",
                detail=None,
                options=[
                    Option("reprendre", "Reprendre", "Résume-moi où j'en étais avec O-jarvis.")
                ],
                confidence=0.5 + n / 10,
                fingerprint=f"test:{n}",
            )
            assert made is not None
            ids.append(made.id)
        return ids

    first, second = client.portal.call(two)
    assert [s["title"] for s in client.get("/v1/suggestions").json()] == [
        "Suggestion 2",
        "Suggestion 1",
    ]
    taken = client.post(f"/v1/suggestions/{first}/accept", json={"option": "reprendre"}).json()
    assert taken["status"] == "acceptee"
    client.portal.call(services.anticipation.drain)
    messages = client.get(f"/v1/conversations/{taken['conversation_id']}").json()["messages"]
    assert messages[0]["content"] == "Résume-moi où j'en étais avec O-jarvis."
    assert messages[1]["role"] == "assistant"
    assert (
        client.post(f"/v1/suggestions/{first}/accept", json={"option": "reprendre"}).status_code
        == 409
    )
    assert client.post(f"/v1/suggestions/{second}/dismiss").json()["status"] == "ecartee"
    assert client.get("/v1/suggestions").json() == []
    events = [e["type"] for e in client.get("/v1/events", params={"limit": 30}).json()]
    assert "suggestion.created" in events
    assert "suggestion.updated" in events
