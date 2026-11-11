"""« Combien de temps ? » : un souvenir de quelques jours s'efface seul ; le code garde sa date."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pytest
from fastapi.testclient import TestClient

from ojarvis.context.engine import ContextEngine, quand
from ojarvis.conversations.store import ConversationStore
from ojarvis.core.clock import utc_now
from ojarvis.db.engine import Database
from ojarvis.db.migrate import upgrade_to_head
from ojarvis.events.bus import EventBus
from ojarvis.llm.providers.fake import FakeProvider, FakeReply
from ojarvis.llm.structured import TOOL_NAME
from ojarvis.llm.types import LLMRequest, ToolCall
from ojarvis.memory.embeddings import HashEmbedder, Kind, Vectors
from ojarvis.memory.store import Echeance, MemoryStore
from ojarvis.services import Services
from ojarvis.systeme1.decideurs import Choix, Decideurs, Systeme1
from ojarvis.systeme1.duree import echeance, jour_dit
from ojarvis.testing.chat import read_sse

PARIS = ZoneInfo("Europe/Paris")
MARDI = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)
JEUDI = date(2026, 11, 12)


def test_only_a_sure_short_lived_fact_gets_an_end() -> None:
    court = Choix("quelques_jours", 0.8)
    assert echeance(None, JEUDI, MARDI, PARIS) == Echeance(None, JEUDI)
    assert echeance(Choix("toujours", 0.9), None, MARDI, PARIS) == Echeance()
    assert echeance(Choix("quelques_jours", 0.4), JEUDI, MARDI, PARIS) == Echeance(None, JEUDI)
    # Jeudi, puis trois jours où il sait que « tu as eu rendez-vous » : effacé lundi à minuit.
    assert echeance(court, JEUDI, MARDI, PARIS) == Echeance(
        datetime(2026, 11, 16, 0, 0, tzinfo=PARIS), JEUDI
    )
    assert echeance(court, None, MARDI, PARIS) == Echeance(MARDI + timedelta(days=7))
    # Une date déjà passée : il le garde au moins jusqu'au lendemain.
    assert echeance(court, date(2026, 11, 5), MARDI, PARIS).expires_at == MARDI + timedelta(days=1)
    # Passée depuis plus d'une semaine, c'est de l'histoire : « a quitté Lyon le 21/09 ».
    assert echeance(court, date(2026, 9, 21), MARDI, PARIS) == Echeance(None, date(2026, 9, 21))


def test_the_date_given_by_the_model_is_read_or_ignored() -> None:
    assert jour_dit("2026-11-12") == JEUDI
    assert jour_dit("jeudi") is None
    assert jour_dit(None) is None


def test_a_dated_memory_says_when_it_is() -> None:
    assert quand(JEUDI, date(2026, 11, 10)) == "jeudi 12 novembre 2026, dans 2 jours"
    assert quand(JEUDI, date(2026, 11, 11)) == "jeudi 12 novembre 2026, demain"
    assert quand(JEUDI, JEUDI) == "jeudi 12 novembre 2026, aujourd'hui"
    assert quand(JEUDI, date(2026, 11, 13)) == "jeudi 12 novembre 2026, passé"


@pytest.fixture
async def stores(tmp_path: Path) -> tuple[MemoryStore, ConversationStore]:
    upgrade_to_head(tmp_path / "m.db")
    db = Database(tmp_path / "m.db")
    memory = MemoryStore(db, HashEmbedder(), EventBus())
    conversations = ConversationStore(db, HashEmbedder())
    await memory.start()
    await conversations.start()
    return memory, conversations


async def test_an_expired_memory_is_forgotten_unless_pinned(
    stores: tuple[MemoryStore, ConversationStore],
) -> None:
    memory, _ = stores
    hier = Echeance(utc_now() - timedelta(minutes=1), JEUDI)
    passe, _ = await memory.add(
        "fait", "L'utilisateur a rendez-vous chez le dentiste.", echeance=hier
    )
    garde, _ = await memory.add("fait", "L'utilisateur part à Rome en avion.", echeance=hier)
    await memory.update(garde.id, pinned=True)
    durable, _ = await memory.add("preference", "L'utilisateur est végétarien.")
    bientot, _ = await memory.add(
        "fait", "L'utilisateur a la grippe.", echeance=Echeance(utc_now() + timedelta(days=3))
    )
    assert sum((await memory.count()).values()) == 4  # compter ne balaie pas

    memory._next_sweep = 0.0  # la minute d'attente entre deux balayages est passée
    assert {r.id for r in await memory.items()} == {garde.id, durable.id, bientot.id}
    assert passe.id not in {hit.record.id for hit in await memory.search("dentiste")}
    assert await memory.forget_expired() == []


async def test_a_lasting_memory_said_again_in_passing_stays_lasting(
    stores: tuple[MemoryStore, ConversationStore],
) -> None:
    memory, _ = stores
    durable, _ = await memory.add("preference", "L'utilisateur est végétarien.")
    redit, cree = await memory.add(
        "preference",
        "L'utilisateur est végétarien.",
        echeance=Echeance(utc_now() + timedelta(days=7)),
    )
    assert not cree
    assert redit.id == durable.id
    assert redit.expires_at is None


async def test_the_context_tells_the_model_the_date_has_passed(
    stores: tuple[MemoryStore, ConversationStore],
) -> None:
    memory, conversations = stores
    await memory.add(
        "fait",
        "L'utilisateur a rendez-vous chez le dentiste le jeudi 12 novembre 2026.",
        echeance=Echeance(utc_now() + timedelta(days=30), JEUDI),
    )
    conversation = await conversations.create("app")

    async def systeme(le: datetime) -> str:
        engine = ContextEngine(
            memory, conversations, embedder_id="hash", clock=lambda: le, timezone=lambda: PARIS
        )
        built = await engine.build(
            conversation_id=conversation.id,
            user_text="Comment s'est passé mon rendez-vous chez le dentiste ?",
            channel="app",
            history=[],
        )
        return built.messages[0].content

    assert "(jeudi 12 novembre 2026, dans 2 jours)" in await systeme(MARDI)
    assert "(jeudi 12 novembre 2026, passé)" in await systeme(MARDI + timedelta(days=3))


class RendezVous:
    """Un seul axe : le mot « rendez-vous » (un fait de quelques jours)."""

    model_id = "rendez-vous"
    dim = 1
    status = "prêt"

    async def embed(self, texts: list[str], kind: Kind) -> Vectors:
        return np.array([[float("rendez-vous" in t)] for t in texts], np.float32)


def decideurs() -> Decideurs:
    # Lignes : « rendez-vous », biais. Sans rendez-vous : un fait ordinaire et durable.
    return Decideurs(
        "rendez-vous",
        {
            "apprendre": np.array([[0, 0], [-4, 4]], np.float32),  # rien, fait
            "sensible": np.array([[0, 0], [4, -4]], np.float32),  # ordinaire, secret
            "duree": np.array([[8, -8], [-4, 4]], np.float32),  # quelques_jours, toujours
        },
        {
            "apprendre": ["rien", "fait"],
            "sensible": ["ordinaire", "secret"],
            "duree": ["quelques_jours", "toujours"],
        },
    )


FAITS: dict[str, Any] = {
    "faits": [
        {"type": "preference", "contenu": "L'utilisateur est végétarien.", "confiance": 0.9},
        {
            "type": "fait",
            "contenu": "L'utilisateur a rendez-vous chez le dentiste le jeudi 12 novembre 2026.",
            "confiance": 0.9,
            "date": "2026-11-12",
        },
    ]
}


def test_each_learned_fact_gets_its_own_duration(
    client: TestClient, services: Services, llm: dict[str, FakeProvider]
) -> None:
    services.chat._systeme1 = Systeme1(RendezVous(), decideurs())
    consignes: list[str] = []

    def script(request: LLMRequest) -> FakeReply:
        if request.tools and request.tools[0].name == TOOL_NAME:
            consignes.extend(m.content for m in request.messages if "Échange :" in m.content)
            return FakeReply(tool_calls=[ToolCall("m", TOOL_NAME, FAITS)])
        if "titre" in request.messages[0].content:
            return FakeReply(text="Dentiste")
        return FakeReply(text="Noté.")

    llm["deepseek"]._script = script
    texte = "Je suis végétarien, et j'ai rendez-vous chez le dentiste jeudi."
    read_sse(client.post("/v1/chat", json={"text": texte}).text)
    assert client.portal is not None
    client.portal.call(services.chat.drain)

    assert consignes[0].startswith("Nous sommes le ")  # pour qu'il calcule « jeudi »
    souvenirs = {m["content"]: m for m in client.get("/v1/memory").json()}
    vegetarien = souvenirs["L'utilisateur est végétarien."]
    dentiste = souvenirs["L'utilisateur a rendez-vous chez le dentiste le jeudi 12 novembre 2026."]
    assert vegetarien["expires_at"] is None
    assert dentiste["expires_at"] is not None
    assert dentiste["event_date"] == "2026-11-12"
