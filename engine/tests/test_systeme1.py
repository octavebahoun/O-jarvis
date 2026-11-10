"""Le Système 1 : les décideurs livrés avec Jarvis, leur lecture, et ce qu'ils évitent."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest

from ojarvis.chat.learning import ChatLearning
from ojarvis.conversations.store import MessageRecord
from ojarvis.memory.embeddings import (
    GEMMA_FILES,
    GEMMA_FOLDER,
    GemmaEmbedder,
    HashEmbedder,
    Kind,
    Vectors,
)
from ojarvis.systeme1.decideurs import Decideurs, Systeme1

MODEL_DIR = Path.home() / ".cache" / "o-jarvis" / "models" / GEMMA_FOLDER
CHAT = {
    "apprendre", "sensible", "bibliotheque", "modele_fort", "mise_a_jour",
    "oublier", "incertain", "recuperer", "resumer", "duree",
}  # fmt: skip


def petits_decideurs(model_id: str = "deux") -> Decideurs:
    """Deux dimensions : « rien » quand la première domine, « fait » quand c'est la seconde."""
    poids = np.array([[4.0, -4.0], [-4.0, 4.0], [0.0, 0.0]], dtype=np.float32)
    return Decideurs(model_id, {"apprendre": poids}, {"apprendre": ["rien", "fait"]})


class DeuxDimensions:
    model_id = "deux"
    dim = 2
    status = "prêt"

    def __init__(self) -> None:
        self.lus: list[str] = []

    async def embed(self, texts: list[str], kind: Kind) -> Vectors:
        self.lus.extend(texts)
        return np.array([[1.0, 0.0] if "?" in t else [0.0, 1.0] for t in texts], np.float32)


def test_the_shipped_weights_cover_the_chat_decisions() -> None:
    decideurs = Decideurs.charger()
    assert decideurs.model_id == GemmaEmbedder.model_id
    assert set(decideurs.decisions) == CHAT
    vecteur = np.zeros(GemmaEmbedder.dim, dtype=np.float32)
    choix = decideurs.sur_message(vecteur)
    assert set(choix) == CHAT - {"mise_a_jour"}
    assert decideurs.mise_a_jour(vecteur, vecteur).reponse in {
        "ajouter", "remplacer", "garder_les_deux", "demander"
    }  # fmt: skip


def test_a_decider_gives_its_likeliest_answer_and_its_confidence() -> None:
    decideurs = petits_decideurs()
    choix = decideurs.decider("apprendre", np.array([1.0, 0.0], np.float32))
    assert choix.reponse == "rien"
    assert 0.99 < choix.confiance <= 1.0
    assert decideurs.decider("apprendre", np.array([0.0, 1.0], np.float32)).reponse == "fait"


async def test_systeme1_stays_silent_when_the_weights_belong_to_another_model() -> None:
    systeme1 = Systeme1(HashEmbedder(), Decideurs.charger())
    assert not systeme1.actif
    assert await systeme1.lire("Je préfère des réponses courtes.") is None
    assert await Systeme1(HashEmbedder(), None).lire("bonjour") is None


async def test_systeme1_reads_the_message_once_for_all_its_decisions() -> None:
    embedder = DeuxDimensions()
    choix = await Systeme1(embedder, petits_decideurs()).lire("Quelle heure est-il ?")
    assert choix is not None
    assert set(choix) == {"apprendre"}
    assert choix["apprendre"].reponse == "rien"
    assert choix["apprendre"].confiance > 0.99
    assert embedder.lus == ["Quelle heure est-il ?"]


class Apprentissage(ChatLearning):
    def __init__(self, systeme1: Systeme1 | None) -> None:
        self._systeme1 = systeme1


def message(texte: str) -> MessageRecord:
    return MessageRecord(
        id=1,
        conversation_id="c",
        role="user",
        content=texte,
        status="ok",
        created_at=datetime.now(UTC),
    )


async def test_nothing_to_learn_skips_the_extraction() -> None:
    sur = Apprentissage(Systeme1(DeuxDimensions(), petits_decideurs()))
    assert not await sur._worth_learning(message("Quelle est la capitale du Pérou ?"))
    assert await sur._worth_learning(message("J'habite à Lyon."))
    # Sans Système 1, ou s'il se tait, le modèle décide seul, comme avant.
    assert await Apprentissage(None)._worth_learning(message("Quelle heure est-il ?"))
    muet = Apprentissage(Systeme1(HashEmbedder(), Decideurs.charger()))
    assert await muet._worth_learning(message("Quelle heure est-il ?"))


async def test_a_doubtful_nothing_still_lets_the_model_decide() -> None:
    hesitant = Decideurs(
        "deux",
        # « rien » l'emporte de peu sur trois réponses : 36 % de confiance seulement.
        {"apprendre": np.array([[0.1, 0.0, 0.0], [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]], np.float32)},
        {"apprendre": ["rien", "fait", "preference"]},
    )
    sur = Apprentissage(Systeme1(DeuxDimensions(), hesitant))
    assert await sur._worth_learning(message("Quelle heure est-il ?"))


async def test_a_text_read_recently_is_not_read_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    embedder = GemmaEmbedder(tmp_path)
    embedder._ready.set()
    lots: list[list[str]] = []

    def run(texts: list[str]) -> Vectors:
        lots.append(texts)
        return np.ones((len(texts), embedder.dim), dtype=np.float32)

    monkeypatch.setattr(embedder, "_run", run)
    await embedder.embed(["bonjour"], "query")
    vecteurs = await embedder.embed(["bonjour", "salut", "bonjour"], "query")
    await embedder.embed(["bonjour"], "passage")  # un autre préfixe, un autre vecteur
    assert vecteurs.shape == (3, embedder.dim)
    assert [len(lot) for lot in lots] == [1, 1, 1]
    assert lots[1] == ["task: search result | query: salut"]


@pytest.mark.skipif(
    not all((MODEL_DIR / name).is_file() for name in GEMMA_FILES),
    reason="EmbeddingGemma 2 absent de cette machine",
)
async def test_real_model_tells_a_question_from_a_preference() -> None:
    systeme1 = Systeme1(GemmaEmbedder(MODEL_DIR), Decideurs.charger())
    question = await systeme1.lire("Quelle est la capitale du Pérou ?")
    preference = await systeme1.lire("Je préfère des réponses courtes, va droit au but.")
    assert question is not None
    assert preference is not None
    assert question["apprendre"].reponse == "rien"
    assert question["recuperer"].reponse == "non"
    assert preference["apprendre"].reponse == "preference"
