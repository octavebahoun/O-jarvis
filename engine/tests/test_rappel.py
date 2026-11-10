"""« Récupérer un souvenir ? » et « Chercher dans la bibliothèque ? » : n'envoyer au modèle que
ce qui sert à la question."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from ojarvis.context.engine import BuiltContext, ContextEngine
from ojarvis.conversations.store import ConversationStore
from ojarvis.db.engine import Database
from ojarvis.db.migrate import upgrade_to_head
from ojarvis.events.bus import EventBus
from ojarvis.memory.embeddings import HashEmbedder, Kind, Vectors
from ojarvis.memory.store import MemoryStore
from ojarvis.systeme1.decideurs import Choix, Decideurs, Systeme1
from ojarvis.systeme1.rappel import Rappel, rappel

INDICES = ("Misérables", "hier", "habite")


def test_only_a_sure_no_holds_anything_back() -> None:
    assert rappel(None) == Rappel()
    assert rappel({"recuperer": Choix("non", 0.9), "bibliotheque": Choix("non", 0.9)}) == Rappel(
        sur_toi=False, bibliotheque=False
    )
    # « Non » à 0,6 pour la bibliothèque : dans le doute, elle est fouillée.
    assert rappel({"recuperer": Choix("oui", 0.6), "bibliotheque": Choix("non", 0.6)}) == Rappel(
        secours=True
    )


class Indices:
    model_id = "indices-rappel"
    dim = len(INDICES)
    status = "prêt"

    async def embed(self, texts: list[str], kind: Kind) -> Vectors:
        return np.array([[float(i in t) for i in INDICES] for t in texts], np.float32)


def decideurs() -> Decideurs:
    # Lignes : Misérables, hier, habite, biais.
    recuperer = np.array([[8, 0], [0, 0], [0, 8], [0, 1]], np.float32)  # non, oui
    bibliotheque = np.array([[0, 0], [0, 8], [0, 0], [2, 0]], np.float32)  # non, oui
    return Decideurs(
        "indices-rappel",
        {"recuperer": recuperer, "bibliotheque": bibliotheque},
        {"recuperer": ["non", "oui"], "bibliotheque": ["non", "oui"]},
    )


@pytest.fixture
async def engine(tmp_path: Path) -> ContextEngine:
    upgrade_to_head(tmp_path / "m.db")
    db = Database(tmp_path / "m.db")
    memory = MemoryStore(db, HashEmbedder(), EventBus())
    conversations = ConversationStore(db, HashEmbedder())
    await memory.start()
    await conversations.start()
    await memory.add("profil", "L'utilisateur s'appelle Octave.")
    epingle, _ = await memory.add("preference", "L'utilisateur veut qu'on le tutoie.")
    await memory.update(epingle.id, kind="profil", pinned=True)
    await memory.add("fait", "L'utilisateur habite à Lyon.")
    ancienne = await conversations.create("app")
    question = await conversations.add_message(
        ancienne.id, "user", "Écris un script pour trier mes photos par date."
    )
    reponse = await conversations.add_message(
        ancienne.id, "assistant", "Voici le script de tri des photos, rangées par date."
    )
    await conversations.index_exchange(question, reponse)
    return ContextEngine(
        memory, conversations, embedder_id="hash", systeme1=Systeme1(Indices(), decideurs())
    )


async def construire(engine: ContextEngine, texte: str) -> tuple[str, BuiltContext]:
    conversation = await engine._conversations.create("app")
    built = await engine.build(
        conversation_id=conversation.id, user_text=texte, channel="app", history=[]
    )
    return built.messages[0].content, built


def fouillee(built: BuiltContext) -> bool:
    return any(item.kind == "souvenir" for item in built.items)


async def test_a_question_for_anyone_sends_nothing_about_you(engine: ContextEngine) -> None:
    systeme, built = await construire(engine, "Qui a écrit Les Misérables ?")
    assert "tutoie" in systeme  # épinglé : toujours envoyé, c'est son choix
    assert "Octave" not in systeme
    assert "Lyon" not in systeme
    assert not fouillee(built)


async def test_the_library_is_searched_when_the_request_points_to_it(
    engine: ContextEngine,
) -> None:
    systeme, built = await construire(engine, "Reprends le script d'hier pour trier les photos")
    assert fouillee(built)
    assert "script de tri des photos" in systeme
    assert "Octave" in systeme


async def test_a_fact_about_you_goes_to_memory_and_the_library_only_as_a_fallback(
    engine: ContextEngine,
) -> None:
    systeme, built = await construire(engine, "Tu te souviens où j'habite ?")
    assert "Lyon" in systeme
    assert not fouillee(built)

    [lyon] = [r for r in await engine._memory.items() if "Lyon" in r.content]
    await engine._memory.delete(lyon.id)
    _, built = await construire(engine, "Tu te souviens où j'habite ?")
    assert fouillee(built)  # la mémoire ne sait rien : le code fouille la bibliothèque
