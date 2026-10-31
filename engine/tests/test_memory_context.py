from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pytest

from ojarvis.context.engine import EMPTY_MEMORY, ContextEngine, estimate_tokens, french_now
from ojarvis.conversations.store import ConversationStore
from ojarvis.core.errors import InvalidInputError, NotFoundError
from ojarvis.db.engine import Database
from ojarvis.db.migrate import upgrade_to_head
from ojarvis.events.bus import EventBus
from ojarvis.memory.embeddings import HashEmbedder
from ojarvis.memory.store import MemoryStore, fts_query
from ojarvis.memory.vectors import VectorIndex


@pytest.fixture
def db(tmp_path: Path) -> Database:
    db_path = tmp_path / "m.db"
    upgrade_to_head(db_path)
    return Database(db_path)


@pytest.fixture
async def memory(db: Database) -> MemoryStore:
    store = MemoryStore(db, HashEmbedder(), EventBus())
    await store.start()
    return store


@pytest.fixture
async def conversations(db: Database) -> ConversationStore:
    store = ConversationStore(db, HashEmbedder())
    await store.start()
    return store


def test_fts_query_is_quoted_and_safe() -> None:
    assert fts_query('café" OR 1=1 -- NEAR(') == '"café" OR "near"'
    assert fts_query("a b") is None
    assert fts_query("Lyon lyon LYON") == '"lyon"'


async def test_hash_embedder_is_deterministic_and_normalized() -> None:
    embedder = HashEmbedder(dim=64)
    a = await embedder.embed(["Octave boit du café", "café du matin"], "passage")
    b = await embedder.embed(["Octave boit du café"], "query")
    assert a.shape == (2, 64)
    assert np.allclose(a[0], b[0])
    assert np.isclose(np.linalg.norm(a[1]), 1.0)


async def test_vector_index_native_and_fallback_agree(db: Database) -> None:
    native = VectorIndex(db, "memory", "test", 3)
    await native.ensure()
    assert native.native
    for owner, vector in {1: [1, 0, 0], 2: [0, 1, 0], 3: [0.9, 0.1, 0]}.items():
        await native.upsert(owner, np.array(vector, dtype=np.float32))
    query = np.array([1, 0, 0], dtype=np.float32)
    native_hits = await native.search(query, 2)
    fallback = VectorIndex(db, "memory", "test", 3)
    fallback.native = False
    brute_hits = await fallback.search(query, 2)
    assert [h[0] for h in native_hits] == [1, 3]
    assert [h[0] for h in brute_hits] == [1, 3]
    assert native_hits[0][1] == pytest.approx(brute_hits[0][1], abs=1e-5)
    assert [h[0] for h in await native.search(query, 2, exclude={1})] == [3, 2]
    await native.delete([1])
    assert [h[0] for h in await native.search(query, 1)] == [3]


async def test_a_message_without_words_does_not_break_the_search(db: Database) -> None:
    # « ok » n'a aucun mot de trois lettres : son vecteur est nul, et sqlite-vec lui donne une
    # distance cosinus vide. Le 31/10, toute recherche échouait alors, et Jarvis ne répondait plus.
    index = VectorIndex(db, "message", "zero", 3)
    await index.ensure()
    await index.upsert(1, np.zeros(3, dtype=np.float32))
    await index.upsert(2, np.array([1, 0, 0], dtype=np.float32))
    assert [h[0] for h in await index.search(np.array([1, 0, 0], dtype=np.float32), 2)] == [2]
    assert await index.search(np.zeros(3, dtype=np.float32), 2) == []


async def test_vector_index_resyncs_from_table(db: Database) -> None:
    index = VectorIndex(db, "memory", "resync", 3)
    await index.ensure()
    await index.upsert(7, np.array([0, 0, 1], dtype=np.float32))
    db.call(lambda s: s.connection().exec_driver_sql("DELETE FROM vec_memory_resync"))
    await VectorIndex(db, "memory", "resync", 3).ensure()
    assert [h[0] for h in await index.search(np.array([0, 0, 1], dtype=np.float32), 1)] == [7]


def test_vector_index_rejects_unsafe_names(db: Database) -> None:
    with pytest.raises(ValueError, match="invalide"):
        VectorIndex(db, "memory; DROP", "x", 3)


async def test_memory_crud_and_events(db: Database) -> None:
    bus = EventBus()
    store = MemoryStore(db, HashEmbedder(), bus)
    await store.start()
    async with bus.stream("memory.*") as queue:
        record, created = await store.add("preference", "  Octave boit du café   chaque matin. ")
        assert created
        assert record.content == "Octave boit du café chaque matin."
        updated = await store.update(record.id, pinned=True, kind="fait")
        assert updated.pinned
        assert updated.kind == "fait"
        await store.delete(record.id)
        types = [queue.get_nowait().type for _ in range(queue.qsize())]
    assert types == ["memory.added", "memory.updated", "memory.deleted"]
    with pytest.raises(NotFoundError):
        await store.get(record.id)
    with pytest.raises(NotFoundError):
        await store.delete(record.id)


async def test_memory_rejects_bad_input(memory: MemoryStore) -> None:
    with pytest.raises(InvalidInputError):
        await memory.add("fait", "   ")
    with pytest.raises(InvalidInputError):
        await memory.add("fait", "x" * 1001)
    with pytest.raises(InvalidInputError):
        await memory.add("humeur", "texte")  # type: ignore[arg-type]


async def test_near_duplicate_updates_instead_of_adding(memory: MemoryStore) -> None:
    first, created = await memory.add("fait", "Octave habite à Lyon.")
    again, created_again = await memory.add("profil", "Octave habite à Lyon !")
    assert created
    assert not created_again
    assert again.id == first.id
    assert again.kind == "profil"
    assert len(await memory.items()) == 1


async def test_memory_hybrid_search(memory: MemoryStore) -> None:
    await memory.add("preference", "Octave boit du café chaque matin.")
    await memory.add("profil", "Octave habite à Lyon.")
    await memory.add("fait", "Le chat d'Octave s'appelle Pixel.")
    hits = await memory.search("café matin", k=3)
    assert hits[0].record.content.startswith("Octave boit du café")
    assert hits[0].via == "les deux"
    assert next(h.record.kind for h in await memory.search("Lyon")) == "profil"
    assert await memory.search("", k=3) == []
    listed = await memory.items(query="pixel")
    assert [r.content for r in listed] == ["Le chat d'Octave s'appelle Pixel."]
    assert await memory.items(query="??") == []
    assert (await memory.count()) == {"preference": 1, "profil": 1, "fait": 1}


async def test_memory_degrades_without_embeddings(db: Database) -> None:
    class Broken(HashEmbedder):
        async def embed(self, texts: list[str], kind: str) -> np.ndarray:
            raise RuntimeError("modèle absent")

    store = MemoryStore(db, Broken(), None)
    await store.start()
    record, _ = await store.add("fait", "Octave joue de la guitare.")
    hits = await store.search("guitare")
    assert [h.record.id for h in hits] == [record.id]
    assert hits[0].via == "mots"


async def test_a_new_embedding_model_embeds_the_memories_already_kept(db: Database) -> None:
    """« Tout Meta » passe d'e5 aux mots hachés : les souvenirs gardent leur recherche par sens."""

    class Before(HashEmbedder):
        model_id = "avant"

    first = MemoryStore(db, Before(), None)
    await first.start()
    record, _ = await first.add("fait", "Octave boit son café sans sucre.")
    after = MemoryStore(db, HashEmbedder(), None)
    await after.start()
    hits = await after.search("café sucre")
    assert [h.record.id for h in hits] == [record.id]
    assert hits[0].via == "les deux"


async def test_touch_records_usage(memory: MemoryStore) -> None:
    record, _ = await memory.add("fait", "Octave apprend Rust.")
    await memory.touch([record.id])
    await memory.touch([])
    again = await memory.get(record.id)
    assert again.use_count == 1
    assert again.last_used_at is not None


async def test_conversation_lifecycle(conversations: ConversationStore) -> None:
    conversation = await conversations.create("app")
    user = await conversations.add_message(conversation.id, "user", "Je bois du café.")
    reply = await conversations.add_message(conversation.id, "assistant", "", status="streaming")
    done = await conversations.finish_message(
        reply.id, content="Noté.", provider="deepseek", model="deepseek-flash", tokens_in=10
    )
    assert done.status == "ok"
    listed = await conversations.recent()
    assert listed[0].message_count == 2
    assert listed[0].preview == "Noté."
    assert [r.id for r in await conversations.recent(query="cafe")] == [conversation.id]
    assert await conversations.recent(query="?") == []
    renamed = await conversations.rename(conversation.id, "  Café   du matin ")
    assert renamed.title == "Café du matin"
    with pytest.raises(InvalidInputError):
        await conversations.rename(conversation.id, "  ")
    await conversations.index_exchange(user, done)
    await conversations.delete(conversation.id)
    with pytest.raises(NotFoundError):
        await conversations.get(conversation.id)
    with pytest.raises(NotFoundError):
        await conversations.add_message(conversation.id, "user", "x")
    assert await conversations.index.search(np.ones(384, dtype=np.float32), 5) == []


async def test_recall_finds_other_conversations_only(conversations: ConversationStore) -> None:
    first = await conversations.create("app")
    user = await conversations.add_message(first.id, "user", "Mon chat Pixel adore les croquettes.")
    reply = await conversations.add_message(first.id, "assistant", "Pixel a bon goût.")
    await conversations.index_exchange(user, reply)
    second = await conversations.create("app")
    found = await conversations.recall("Que mange Pixel le chat ?", exclude_conversation=second.id)
    assert found
    assert "Jarvis : Pixel a bon goût." in found[0].message.content
    assert await conversations.recall("Pixel", exclude_conversation=first.id) == []


def test_estimate_tokens_and_french_date() -> None:
    assert estimate_tokens("") == 1
    assert estimate_tokens("x" * 36) == 10
    moment = datetime(2026, 3, 29, 1, 30, tzinfo=UTC)  # jour du passage à l'heure d'été
    text = french_now(moment, ZoneInfo("Europe/Paris"))
    assert text == "Nous sommes le dimanche 29 mars 2026, il est 3 h 30 (fuseau Europe/Paris)."


async def test_context_selects_within_budget(
    memory: MemoryStore, conversations: ConversationStore
) -> None:
    await memory.add("profil", "L'utilisateur s'appelle Octave.")
    await memory.add("preference", "Octave boit du café chaque matin.")
    await memory.add("fait", "Octave a un vélo rouge.")
    conversation = await conversations.create("app")
    for i in range(30):
        await conversations.add_message(conversation.id, "user", f"message ancien numéro {i} " * 20)
    history = await conversations.messages(conversation.id)
    engine = ContextEngine(
        memory,
        conversations,
        embedder_id="hash",
        clock=lambda: datetime(2026, 10, 15, 8, 0, tzinfo=UTC),
        timezone=lambda: ZoneInfo("Europe/Paris"),
    )
    built = await engine.build(
        conversation_id=conversation.id,
        user_text="Qu'est-ce que je bois le matin ? café",
        channel="voice",
        history=history,
        budget=900,
    )
    system = built.messages[0].content
    assert "Tu réponds à voix haute" in system
    assert "jeudi 15 octobre 2026, il est 10 h 00" in system
    assert "\n<memoire>\n" in system
    assert "[profil] L'utilisateur s'appelle Octave." in system
    assert "café" in system
    assert built.messages[-1].content.startswith("Qu'est-ce que je bois")
    assert built.used <= built.budget
    kinds = {i.kind for i in built.items}
    assert {"systeme", "profil", "historique"} <= kinds
    dropped = [i for i in built.items if not i.kept]
    assert any(i.reason == "budget dépassé" for i in dropped)
    assert len(built.memory_ids) >= 2
    summary = built.summary()
    assert summary["kept"] + summary["dropped"] == len(built.items)  # type: ignore[operator]


async def test_context_without_memory_says_so(
    memory: MemoryStore, conversations: ConversationStore
) -> None:
    engine = ContextEngine(memory, conversations, embedder_id="hash")
    conversation = await conversations.create("app")
    built = await engine.build(
        conversation_id=conversation.id, user_text="Bonjour", channel="app", history=[]
    )
    # Mémoire vide dite noir sur blanc : le modèle ne doit pas combler le vide en inventant.
    assert EMPTY_MEMORY in built.messages[0].content
    assert "markdown" in built.messages[0].content
    assert [m.role for m in built.messages] == ["system", "user"]
