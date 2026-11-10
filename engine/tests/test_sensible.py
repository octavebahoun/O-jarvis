"""« Sensible ? » : un secret n'est jamais gardé ; un souvenir personnel n'est envoyé au modèle
que quand la question en parle."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest
from fastapi.testclient import TestClient

from ojarvis.chat.learning import Tri
from ojarvis.context.engine import ContextEngine
from ojarvis.conversations.store import ConversationStore
from ojarvis.db.engine import Database
from ojarvis.db.migrate import upgrade_to_head
from ojarvis.events.bus import EventBus
from ojarvis.llm.providers.fake import FakeProvider, FakeReply
from ojarvis.llm.structured import TOOL_NAME
from ojarvis.llm.types import LLMRequest, ToolCall
from ojarvis.memory.embeddings import HashEmbedder, Kind, Vectors
from ojarvis.memory.store import MemoryStore
from ojarvis.services import Services
from ojarvis.systeme1.decideurs import Decideurs, Systeme1
from ojarvis.testing.chat import read_sse
from tests.test_systeme1 import Apprentissage, message


class MotsCles:
    """Un axe par indice : « ? » (une question), « PIN » (un secret), « diabète » (personnel)."""

    model_id = "mots-cles"
    dim = 3
    status = "prêt"

    async def embed(self, texts: list[str], kind: Kind) -> Vectors:
        return np.array(
            [[float("?" in t), float("PIN" in t), float("diabète" in t)] for t in texts],
            np.float32,
        )


def decideurs() -> Decideurs:
    # Lignes : question, secret, personnel, biais. Sans indice : un fait ordinaire.
    apprendre = np.array([[8, -8], [-4, 4], [-4, 4], [-2, 2]], np.float32)  # rien, fait
    sensible = np.array(  # ordinaire, personnel, secret
        [[0, 0, 0], [-4, -4, 8], [-4, 8, -4], [2, 0, 0]], np.float32
    )
    return Decideurs(
        "mots-cles",
        {"apprendre": apprendre, "sensible": sensible},
        {"apprendre": ["rien", "fait"], "sensible": ["ordinaire", "personnel", "secret"]},
    )


def systeme1() -> Systeme1:
    return Systeme1(MotsCles(), decideurs())


async def test_a_secret_is_never_learned_and_personal_facts_are_marked() -> None:
    tri = Apprentissage(systeme1())._triage
    assert await tri(message("Mon PIN de carte est 4821, retiens-le.")) == Tri(apprendre=False)
    assert await tri(message("J'ai du diabète depuis 2019.")) == Tri(personnel=True)
    assert await tri(message("J'aime le jazz.")) == Tri()
    assert await tri(message("Quelle heure est-il ?")) == Tri(apprendre=False)


def script(fait: str, demandes: list[str]) -> Any:
    def repondre(request: LLMRequest) -> FakeReply:
        if request.tools and request.tools[0].name == TOOL_NAME:
            demandes.append("extraction")
            extraction = {"faits": [{"type": "profil", "contenu": fait, "confiance": 0.9}]}
            return FakeReply(tool_calls=[ToolCall("m", TOOL_NAME, extraction)])
        if "titre" in request.messages[0].content:
            return FakeReply(text="Santé")
        return FakeReply(text="Noté.")

    return repondre


def test_the_chat_keeps_personal_facts_apart_and_secrets_out(
    client: TestClient, services: Services, llm: dict[str, FakeProvider]
) -> None:
    services.chat._systeme1 = systeme1()
    assert client.portal is not None
    demandes: list[str] = []

    llm["deepseek"]._script = script("L'utilisateur a du diabète depuis 2019.", demandes)
    read_sse(client.post("/v1/chat", json={"text": "J'ai du diabète depuis 2019."}).text)
    client.portal.call(services.chat.drain)
    [souvenir] = client.get("/v1/memory").json()
    assert souvenir["personnel"] is True
    assert demandes == ["extraction"]

    llm["deepseek"]._script = script("Le PIN de l'utilisateur est 4821.", demandes)
    read_sse(client.post("/v1/chat", json={"text": "Mon PIN de carte est 4821."}).text)
    client.portal.call(services.chat.drain)
    assert demandes == ["extraction"]  # le modèle n'a même pas été sollicité
    assert len(client.get("/v1/memory").json()) == 1

    # La marque se retire (ou se pose) à la main, depuis la page Mémoire.
    retire = client.patch(f"/v1/memory/{souvenir['id']}", json={"personnel": False})
    assert retire.json()["personnel"] is False


@pytest.fixture
async def stores(tmp_path: Path) -> tuple[MemoryStore, ConversationStore]:
    upgrade_to_head(tmp_path / "m.db")
    db = Database(tmp_path / "m.db")
    memory = MemoryStore(db, HashEmbedder(), EventBus())
    conversations = ConversationStore(db, HashEmbedder())
    await memory.start()
    await conversations.start()
    return memory, conversations


async def test_a_personal_memory_is_sent_only_when_the_question_is_about_it(
    stores: tuple[MemoryStore, ConversationStore],
) -> None:
    memory, conversations = stores
    await memory.add("profil", "L'utilisateur s'appelle Octave.")
    await memory.add("profil", "L'utilisateur habite au 12 rue des Lilas à Lyon.", personnel=True)
    epingle, _ = await memory.add("profil", "L'utilisateur est diabétique.", personnel=True)
    await memory.update(epingle.id, pinned=True)  # épinglé : toujours envoyé, c'est son choix
    engine = ContextEngine(memory, conversations, embedder_id="hash")
    conversation = await conversations.create("app")

    async def systeme(question: str) -> str:
        built = await engine.build(
            conversation_id=conversation.id, user_text=question, channel="app", history=[]
        )
        return built.messages[0].content

    ailleurs = await systeme("Écris un poème sur la mer pour mon cours")
    assert "Octave" in ailleurs
    assert "diabétique" in ailleurs
    assert "Lilas" not in ailleurs
    assert "Lilas" in await systeme("Tu sais à quelle adresse j'habite, rue des Lilas ?")


async def test_a_duplicate_of_a_personal_memory_stays_personal(
    stores: tuple[MemoryStore, ConversationStore],
) -> None:
    memory, _ = stores
    premier, _ = await memory.add("fait", "L'utilisateur a du diabète.", personnel=True)
    redit, cree = await memory.add("fait", "L'utilisateur a du diabète.")
    assert not cree
    assert redit.id == premier.id
    assert redit.personnel
