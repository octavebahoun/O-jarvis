"""Mémoire long terme : faits, préférences et profil. Visible, modifiable, supprimable.

La recherche combine le sens (vecteurs) et les mots (plein texte), pour retrouver aussi
bien « qu'est-ce que je bois le matin ? » que « café ».
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import numpy as np
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from ojarvis.core.clock import utc_now
from ojarvis.core.errors import InvalidInputError, NotFoundError
from ojarvis.core.logs import get_logger
from ojarvis.db.engine import Database
from ojarvis.db.models import Embedding, MemoryItem
from ojarvis.events.bus import EventBus
from ojarvis.memory.embeddings import Embedder, Vectors
from ojarvis.memory.vectors import VectorIndex
from ojarvis.security.redaction import REDACTOR

log = get_logger(__name__)

#: « impasse » : un correctif qui a échoué, noté par le mode shell
#: (`ojarvis_shell.tools.impasses`) ;
#: jamais injecté d'office dans le contexte.
MemoryKind = Literal["fait", "preference", "profil", "impasse", "machine"]
MEMORY_KINDS: tuple[MemoryKind, ...] = ("fait", "preference", "profil", "impasse", "machine")
#: Ne se fondent jamais dans un souvenir d'un autre type, ni l'inverse : une impasse, une
#: ligne de la carte de la machine.
SEPARATE_KINDS = frozenset({"impasse", "machine"})
#: Au-delà de cette similarité, un nouveau souvenir met à jour l'ancien au lieu de doublonner.
DUPLICATE_SIMILARITY = 0.95
MAX_CONTENT = 1000

_FTS_WORD = re.compile(r"\w{3,}", re.UNICODE)


def fts_query(text_value: str) -> str | None:
    """Requête FTS5 sûre : mots entre guillemets, reliés par OR (aucune syntaxe utilisateur)."""
    words = list(dict.fromkeys(w.lower() for w in _FTS_WORD.findall(text_value)))[:12]
    if not words:
        return None
    return " OR ".join(f'"{w}"' for w in words)


@dataclass(frozen=True, slots=True)
class MemoryRecord:
    id: int
    kind: str
    content: str
    source: str
    confidence: float
    pinned: bool
    created_at: datetime
    updated_at: datetime
    last_used_at: datetime | None
    use_count: int

    @classmethod
    def of(cls, item: MemoryItem) -> MemoryRecord:
        return cls(
            id=item.id,
            kind=item.kind,
            content=item.content,
            source=item.source,
            confidence=item.confidence,
            pinned=item.pinned,
            created_at=item.created_at,
            updated_at=item.updated_at,
            last_used_at=item.last_used_at,
            use_count=item.use_count,
        )


@dataclass(frozen=True, slots=True)
class MemoryHit:
    record: MemoryRecord
    score: float
    via: str  # sens | mots | les deux


def _clean(content: str) -> str:
    cleaned = " ".join(content.split())
    if not cleaned or len(cleaned) > MAX_CONTENT:
        raise InvalidInputError("Un souvenir fait entre 1 et 1000 caractères.")
    return cleaned


class MemoryStore:
    def __init__(self, db: Database, embedder: Embedder, bus: EventBus | None = None) -> None:
        self._db = db
        self._embedder = embedder
        self._bus = bus
        self.index = VectorIndex(db, "memory", embedder.model_id, embedder.dim)

    async def start(self) -> None:
        await self.index.ensure()
        await self._embed_missing()

    async def _embed_missing(self) -> None:
        """Les souvenirs sans vecteur pour ce modèle (le profil « Tout Meta » change de
        modèle d'embeddings) le reçoivent au démarrage."""

        def missing(session: Session) -> list[tuple[int, str]]:
            indexed = select(Embedding.owner_id).where(
                Embedding.owner_type == self.index.owner_type,
                Embedding.model == self.index.model_id,
            )
            rows = session.execute(
                select(MemoryItem.id, MemoryItem.content).where(MemoryItem.id.not_in(indexed))
            ).all()
            return [(row.id, row.content) for row in rows]

        for item_id, content in await self._db.run(missing):
            vector = await self._embed_passage(content)
            if vector is None:
                return  # le modèle n'est pas prêt : on réessaiera au prochain démarrage
            await self.index.upsert(item_id, vector)

    async def _publish(self, event: str, payload: dict[str, object]) -> None:
        if self._bus is not None:
            await self._bus.publish(event, payload, source="memoire")

    async def _embed_passage(self, content: str) -> Vectors | None:
        try:
            vectors = await self._embedder.embed([content], "passage")
        except Exception:
            # La mémoire reste utilisable par les mots si le modèle n'est pas prêt.
            log.warning("memory_embedding_unavailable")
            return None
        vector: Vectors = np.asarray(vectors[0], dtype=np.float32)
        return vector

    async def add(
        self,
        kind: MemoryKind,
        content: str,
        *,
        source: str = "manuel",
        source_message_id: int | None = None,
        confidence: float = 1.0,
        pinned: bool = False,
    ) -> tuple[MemoryRecord, bool]:
        """Ajoute un souvenir, ou met à jour un quasi-doublon. Renvoie (souvenir, créé ?)."""
        content = REDACTOR.redact(content)  # jamais de clé dans la mémoire
        if kind not in MEMORY_KINDS:
            raise InvalidInputError("Type de souvenir inconnu.")
        cleaned = _clean(content)
        vector = await self._embed_passage(cleaned)
        if vector is not None:
            duplicate = await self._duplicate(kind, cleaned, vector)
            if duplicate is not None:
                updated = await self.update(duplicate, content=cleaned, kind=kind)
                return updated, False
        now = utc_now()

        def insert(session: Session) -> MemoryRecord:
            item = MemoryItem(
                kind=kind,
                content=cleaned,
                source=source,
                source_message_id=source_message_id,
                confidence=max(0.0, min(confidence, 1.0)),
                pinned=pinned,
                created_at=now,
                updated_at=now,
                last_used_at=None,
                use_count=0,
            )
            session.add(item)
            session.flush()
            return MemoryRecord.of(item)

        record = await self._db.run(insert)
        if vector is not None:
            await self.index.upsert(record.id, vector)
        await self._publish("memory.added", {"id": record.id, "kind": kind, "source": source})
        return record, True

    async def _duplicate(self, kind: MemoryKind, cleaned: str, vector: Vectors) -> int | None:
        """Le souvenir que celui-ci répète, s'il y en a un. Une impasse ne se fond que dans
        la même impasse : deux correctifs tentés pour une même erreur, ou la même erreur dans
        deux projets, se ressemblent beaucoup, et chacun compte."""
        for item_id, score in await self.index.search(vector, 5):
            if score < DUPLICATE_SIMILARITY:
                break
            existing = await self.get(item_id)
            if (
                existing.kind in SEPARATE_KINDS or kind in SEPARATE_KINDS
            ) and existing.kind != kind:
                continue
            if kind == "impasse" and existing.content != cleaned:
                continue
            return item_id
        return None

    async def update(
        self,
        item_id: int,
        *,
        content: str | None = None,
        kind: MemoryKind | None = None,
        pinned: bool | None = None,
    ) -> MemoryRecord:
        if kind is not None and kind not in MEMORY_KINDS:
            raise InvalidInputError("Type de souvenir inconnu.")
        cleaned = _clean(REDACTOR.redact(content)) if content is not None else None

        def write(session: Session) -> tuple[MemoryRecord, bool]:
            item = session.get(MemoryItem, item_id)
            if item is None:
                raise NotFoundError("Souvenir introuvable.")
            changed_text = cleaned is not None and cleaned != item.content
            if cleaned is not None:
                item.content = cleaned
            if kind is not None:
                item.kind = kind
            if pinned is not None:
                item.pinned = pinned
            item.updated_at = utc_now()
            return MemoryRecord.of(item), changed_text

        record, changed_text = await self._db.run(write)
        if changed_text:
            vector = await self._embed_passage(record.content)
            if vector is not None:
                await self.index.upsert(record.id, vector)
        await self._publish("memory.updated", {"id": record.id, "kind": record.kind})
        return record

    async def delete(self, item_id: int) -> None:
        def remove(session: Session) -> None:
            item = session.get(MemoryItem, item_id)
            if item is None:
                raise NotFoundError("Souvenir introuvable.")
            session.delete(item)

        await self._db.run(remove)
        await self.index.delete([item_id])
        await self._publish("memory.deleted", {"id": item_id})

    async def get(self, item_id: int) -> MemoryRecord:
        def read(session: Session) -> MemoryRecord:
            item = session.get(MemoryItem, item_id)
            if item is None:
                raise NotFoundError("Souvenir introuvable.")
            return MemoryRecord.of(item)

        return await self._db.run(read)

    async def items(
        self, *, kind: str | None = None, query: str | None = None, limit: int = 500
    ) -> list[MemoryRecord]:
        match = fts_query(query) if query else None

        def read(session: Session) -> list[MemoryRecord]:
            stmt = select(MemoryItem).order_by(
                MemoryItem.pinned.desc(), MemoryItem.updated_at.desc()
            )
            if kind:
                stmt = stmt.where(MemoryItem.kind == kind)
            if query is not None:
                if match is None:
                    return []
                ids = session.execute(
                    text("SELECT rowid FROM memory_items_fts WHERE memory_items_fts MATCH :q"),
                    {"q": match},
                ).scalars()
                stmt = stmt.where(MemoryItem.id.in_(list(ids)))
            return [MemoryRecord.of(i) for i in session.scalars(stmt.limit(limit))]

        return await self._db.run(read)

    async def count(self) -> dict[str, int]:
        def read(session: Session) -> dict[str, int]:
            rows = session.execute(
                select(MemoryItem.kind, func.count()).group_by(MemoryItem.kind)
            ).all()
            return {str(k): int(n) for k, n in rows}

        return await self._db.run(read)

    async def search(self, query: str, *, k: int = 8, min_score: float = 0.0) -> list[MemoryHit]:
        """Recherche hybride : sens (vecteurs) et mots (plein texte), fusionnés."""
        scores: dict[int, tuple[float, str]] = {}
        try:
            vector = (await self._embedder.embed([query], "query"))[0]
            for item_id, score in await self.index.search(vector, k * 2):
                scores[item_id] = (score, "sens")
        except Exception:
            log.warning("memory_vector_search_unavailable")
        match = fts_query(query)
        if match:

            def keyword(session: Session) -> list[int]:
                return [
                    int(r)
                    for r in session.execute(
                        text(
                            "SELECT rowid FROM memory_items_fts WHERE memory_items_fts MATCH :q "
                            "ORDER BY rank LIMIT :k"
                        ),
                        {"q": match, "k": k},
                    ).scalars()
                ]

            for rank, item_id in enumerate(await self._db.run(keyword)):
                bonus = 0.04 * (1 - rank / max(k, 1))
                previous = scores.get(item_id)
                if previous is None:
                    scores[item_id] = (0.78 + bonus, "mots")
                else:
                    scores[item_id] = (previous[0] + bonus, "les deux")
        ranked = sorted(scores.items(), key=lambda kv: -kv[1][0])
        ranked = [(i, s) for i, s in ranked if s[0] >= min_score][:k]
        if not ranked:
            return []

        def load(session: Session) -> dict[int, MemoryRecord]:
            items = session.scalars(
                select(MemoryItem).where(MemoryItem.id.in_([i for i, _ in ranked]))
            )
            return {i.id: MemoryRecord.of(i) for i in items}

        records = await self._db.run(load)
        return [MemoryHit(records[i], round(s[0], 4), s[1]) for i, s in ranked if i in records]

    async def touch(self, ids: list[int]) -> None:
        """Note l'usage des souvenirs envoyés au modèle (utile pour l'anticipation)."""
        if not ids:
            return
        now = utc_now()

        def write(session: Session) -> None:
            for item in session.scalars(select(MemoryItem).where(MemoryItem.id.in_(ids))):
                item.last_used_at = now
                item.use_count += 1

        await self._db.run(write)
