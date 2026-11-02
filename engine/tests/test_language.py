"""La langue de la demande : l'anglais est dit au modèle, une relance courte garde la langue
de la demande d'avant (Muse Wonder, le 2/11/2026, répondait en français à de l'anglais)."""

from __future__ import annotations

from pathlib import Path

import pytest

from ojarvis.context.engine import ContextEngine
from ojarvis.context.language import ENGLISH_HINT, english_requested, is_english
from ojarvis.conversations.store import ConversationStore
from ojarvis.db.engine import Database
from ojarvis.db.migrate import upgrade_to_head
from ojarvis.events.bus import EventBus
from ojarvis.memory.embeddings import HashEmbedder
from ojarvis.memory.store import MemoryStore


def test_english_french_and_unknown() -> None:
    assert is_english("Build a polished one-page website for a tea shop in Lyon.")
    assert is_english("How much disk space is left?")
    assert is_english("Combien de place reste-t-il sur le disque ?") is False
    assert is_english("Crée notes.txt dans ce dossier avec une liste.") is False
    assert is_english("Continue.") is None
    assert is_english("o") is None


def test_a_short_follow_up_keeps_the_language_of_the_request() -> None:
    assert english_requested(["Continue.", "Write a shopping list in notes.txt please."])
    assert not english_requested(["Continue.", "Écris une liste de courses dans notes.txt."])
    assert not english_requested(["ok"])


@pytest.fixture
async def engine(tmp_path: Path) -> tuple[ContextEngine, ConversationStore]:
    db_path = tmp_path / "m.db"
    upgrade_to_head(db_path)
    db = Database(db_path)
    memory = MemoryStore(db, HashEmbedder(), EventBus())
    await memory.start()
    conversations = ConversationStore(db, HashEmbedder())
    await conversations.start()
    return ContextEngine(memory, conversations, embedder_id="hash"), conversations


async def test_the_model_is_told_to_answer_in_english(
    engine: tuple[ContextEngine, ConversationStore],
) -> None:
    context, conversations = engine
    conversation = await conversations.create("shell")
    english = await context.build(
        conversation_id=conversation.id,
        user_text="Build a one-page site for a tea shop in this folder.",
        channel="shell",
        history=[],
    )
    assert ENGLISH_HINT in english.messages[0].content
    french = await context.build(
        conversation_id=conversation.id,
        user_text="Fais un site d'une page pour un salon de thé dans ce dossier.",
        channel="shell",
        history=[],
    )
    assert ENGLISH_HINT not in french.messages[0].content
