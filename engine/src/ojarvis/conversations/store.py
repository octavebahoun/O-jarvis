"""Conversations et messages, avec recherche plein texte et « souvenirs » par le sens."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from ojarvis.core.clock import utc_now
from ojarvis.core.errors import InvalidInputError, NotFoundError
from ojarvis.core.logs import get_logger
from ojarvis.core.text import plain_text
from ojarvis.db.engine import Database
from ojarvis.db.models import ChatMessage, Conversation, Embedding
from ojarvis.memory.embeddings import Embedder
from ojarvis.memory.store import fts_query
from ojarvis.memory.vectors import VectorIndex

log = get_logger(__name__)

DEFAULT_TITLE = "Nouvelle conversation"


@dataclass(frozen=True, slots=True)
class ConversationRecord:
    id: str
    title: str
    channel: str
    created_at: datetime
    updated_at: datetime
    message_count: int = 0
    preview: str = ""


@dataclass(frozen=True, slots=True)
class MessageRecord:
    id: int
    conversation_id: str
    role: str
    content: str
    status: str
    created_at: datetime
    provider: str | None = None
    model: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    cost_usd: float | None = None
    latency_ms: float | None = None
    trace_id: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def of(cls, m: ChatMessage) -> MessageRecord:
        return cls(
            id=m.id,
            conversation_id=m.conversation_id,
            role=m.role,
            content=m.content,
            status=m.status,
            created_at=m.created_at,
            provider=m.provider,
            model=m.model,
            tokens_in=m.tokens_in,
            tokens_out=m.tokens_out,
            cost_usd=m.cost_usd,
            latency_ms=m.latency_ms,
            trace_id=m.trace_id,
            meta=dict(m.meta or {}),
        )


@dataclass(frozen=True, slots=True)
class Recollection:
    """Un passage d'une autre conversation, retrouvé par le sens."""

    message: MessageRecord
    conversation_title: str
    score: float


class ConversationStore:
    def __init__(self, db: Database, embedder: Embedder) -> None:
        self._db = db
        self._embedder = embedder
        self.index = VectorIndex(db, "message", embedder.model_id, embedder.dim)
        #: Tes demandes seules (sans les réponses, qui varient) : pour repérer ce qui revient.
        self.requests = VectorIndex(db, "requete", embedder.model_id, embedder.dim)

    async def start(self) -> None:
        await self.index.ensure()
        await self.requests.ensure()

    async def create(self, channel: str = "app", title: str = DEFAULT_TITLE) -> ConversationRecord:
        now = utc_now()
        conversation_id = uuid.uuid4().hex

        def insert(session: Session) -> ConversationRecord:
            session.add(
                Conversation(
                    id=conversation_id, title=title, channel=channel, created_at=now, updated_at=now
                )
            )
            return ConversationRecord(conversation_id, title, channel, now, now)

        return await self._db.run(insert)

    async def get(self, conversation_id: str) -> ConversationRecord:
        def read(session: Session) -> ConversationRecord:
            c = session.get(Conversation, conversation_id)
            if c is None:
                raise NotFoundError("Conversation introuvable.")
            count = session.scalar(
                select(func.count()).where(ChatMessage.conversation_id == conversation_id)
            )
            return ConversationRecord(
                c.id, c.title, c.channel, c.created_at, c.updated_at, int(count or 0)
            )

        return await self._db.run(read)

    async def recent(
        self, *, limit: int = 50, before: datetime | None = None, query: str | None = None
    ) -> list[ConversationRecord]:
        match = fts_query(query) if query else None

        def read(session: Session) -> list[ConversationRecord]:
            stmt = select(Conversation).where(Conversation.archived.is_(False))
            if before is not None:
                stmt = stmt.where(Conversation.updated_at < before)
            if query is not None:
                if match is None:
                    return []
                hits = session.execute(
                    text(
                        "SELECT DISTINCT m.conversation_id FROM messages_fts f "
                        "JOIN messages m ON m.id = f.rowid WHERE messages_fts MATCH :q"
                    ),
                    {"q": match},
                ).scalars()
                stmt = stmt.where(Conversation.id.in_(list(hits)))
            conversations = list(
                session.scalars(stmt.order_by(Conversation.updated_at.desc()).limit(limit))
            )
            result: list[ConversationRecord] = []
            for c in conversations:
                count = session.scalar(
                    select(func.count()).where(ChatMessage.conversation_id == c.id)
                )
                last = session.scalar(
                    select(ChatMessage.content)
                    .where(ChatMessage.conversation_id == c.id)
                    .order_by(ChatMessage.id.desc())
                    .limit(1)
                )
                result.append(
                    ConversationRecord(
                        c.id,
                        c.title,
                        c.channel,
                        c.created_at,
                        c.updated_at,
                        int(count or 0),
                        plain_text(last or "")[:160],
                    )
                )
            return result

        return await self._db.run(read)

    async def rename(self, conversation_id: str, title: str) -> ConversationRecord:
        cleaned = " ".join(title.split())[:200]
        if not cleaned:
            raise InvalidInputError("Le titre ne peut pas être vide.")

        def write(session: Session) -> None:
            c = session.get(Conversation, conversation_id)
            if c is None:
                raise NotFoundError("Conversation introuvable.")
            c.title = cleaned

        await self._db.run(write)
        return await self.get(conversation_id)

    async def delete(self, conversation_id: str) -> None:
        def remove(session: Session) -> list[int]:
            c = session.get(Conversation, conversation_id)
            if c is None:
                raise NotFoundError("Conversation introuvable.")
            ids = list(
                session.scalars(
                    select(ChatMessage.id).where(ChatMessage.conversation_id == conversation_id)
                )
            )
            session.execute(
                delete(ChatMessage).where(ChatMessage.conversation_id == conversation_id)
            )
            session.delete(c)
            return ids

        message_ids = await self._db.run(remove)
        await self.index.delete(message_ids)
        await self.requests.delete(message_ids)

    async def add_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        *,
        status: str = "ok",
        meta: dict[str, Any] | None = None,
    ) -> MessageRecord:
        now = utc_now()

        def insert(session: Session) -> MessageRecord:
            c = session.get(Conversation, conversation_id)
            if c is None:
                raise NotFoundError("Conversation introuvable.")
            message = ChatMessage(
                conversation_id=conversation_id,
                role=role,
                content=content,
                status=status,
                created_at=now,
                meta=meta or {},
            )
            session.add(message)
            c.updated_at = now
            session.flush()
            return MessageRecord.of(message)

        return await self._db.run(insert)

    async def finish_message(
        self,
        message_id: int,
        *,
        content: str,
        status: str = "ok",
        provider: str | None = None,
        model: str | None = None,
        tokens_in: int | None = None,
        tokens_out: int | None = None,
        cost_usd: float | None = None,
        latency_ms: float | None = None,
        trace_id: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> MessageRecord:
        def write(session: Session) -> MessageRecord:
            m = session.get(ChatMessage, message_id)
            if m is None:
                raise NotFoundError("Message introuvable.")
            m.content = content
            m.status = status
            m.provider = provider
            m.model = model
            m.tokens_in = tokens_in
            m.tokens_out = tokens_out
            m.cost_usd = cost_usd
            m.latency_ms = latency_ms
            m.trace_id = trace_id
            if meta:
                m.meta = {**(m.meta or {}), **meta}
            return MessageRecord.of(m)

        return await self._db.run(write)

    async def summary(self, conversation_id: str) -> tuple[str, int] | None:
        """Le résumé de la conversation et le dernier message qu'il couvre, s'il y en a un."""

        def read(session: Session) -> tuple[str, int] | None:
            c = session.get(Conversation, conversation_id)
            if c is None or not c.summary or c.summary_upto is None:
                return None
            return c.summary, c.summary_upto

        return await self._db.run(read)

    async def set_summary(self, conversation_id: str, summary: str, upto: int) -> None:
        def write(session: Session) -> None:
            c = session.get(Conversation, conversation_id)
            if c is None:
                raise NotFoundError("Conversation introuvable.")
            c.summary = summary
            c.summary_upto = upto

        await self._db.run(write)

    async def messages(self, conversation_id: str, *, limit: int = 200) -> list[MessageRecord]:
        def read(session: Session) -> list[MessageRecord]:
            rows = session.scalars(
                select(ChatMessage)
                .where(ChatMessage.conversation_id == conversation_id)
                .order_by(ChatMessage.id.desc())
                .limit(limit)
            )
            return [MessageRecord.of(m) for m in reversed(list(rows))]

        return await self._db.run(read)

    async def index_exchange(self, user: MessageRecord, assistant: MessageRecord) -> bool:
        """Vectorise un échange (question + réponse) pour le retrouver plus tard par le sens.
        Faux si le modèle de sens n'est pas prêt."""
        passage = f"{user.content}\n{assistant.content}"[:2000]
        try:
            vector = (await self._embedder.embed([passage], "passage"))[0]
        except Exception:
            log.warning("message_embedding_unavailable")
            return False
        await self.index.upsert(user.id, vector)
        return True

    async def index_missing(self) -> int:
        """Les échanges passés sans vecteur pour ce modèle (le modèle de sens a changé) le
        reçoivent, du plus récent au plus ancien. Rend le nombre d'échanges indexés ; s'arrête
        si le modèle n'est pas prêt (la suite se fera au prochain démarrage)."""

        def missing(session: Session) -> list[tuple[MessageRecord, MessageRecord]]:
            indexed = select(Embedding.owner_id).where(
                Embedding.owner_type == self.index.owner_type,
                Embedding.model == self.index.model_id,
            )
            asked = session.scalars(
                select(ChatMessage)
                .where(ChatMessage.role == "user", ChatMessage.id.not_in(indexed))
                .order_by(ChatMessage.id.desc())
            ).all()
            pairs = []
            for user in asked:
                reply = session.scalar(
                    select(ChatMessage)
                    .where(
                        ChatMessage.conversation_id == user.conversation_id,
                        ChatMessage.id > user.id,
                        ChatMessage.role == "assistant",
                    )
                    .order_by(ChatMessage.id)
                    .limit(1)
                )
                if reply is not None and reply.status == "ok" and reply.content:
                    pairs.append((MessageRecord.of(user), MessageRecord.of(reply)))
            return pairs

        done = 0
        for user, reply in await self._db.run(missing):
            if not await self.index_exchange(user, reply):
                break
            done += 1
        return done

    async def index_request(self, message: MessageRecord) -> None:
        try:
            vector = (await self._embedder.embed([message.content[:1000]], "query"))[0]
        except Exception:
            log.warning("request_embedding_unavailable")
            return
        await self.requests.upsert(message.id, vector)

    async def similar_requests(
        self, message: MessageRecord, *, since: datetime, k: int = 20
    ) -> list[tuple[MessageRecord, float]]:
        """Tes demandes passées proches de celle-ci (elle-même exclue), les plus proches d'abord."""
        try:
            vector = (await self._embedder.embed([message.content[:1000]], "query"))[0]
        except Exception:
            return []
        hits = dict(await self.requests.search(vector, k + 1))
        hits.pop(message.id, None)
        if not hits:
            return []

        def load(session: Session) -> list[tuple[MessageRecord, float]]:
            rows = session.scalars(
                select(ChatMessage).where(
                    ChatMessage.id.in_(list(hits)),
                    ChatMessage.role == "user",
                    ChatMessage.created_at >= since,
                )
            )
            found = [(MessageRecord.of(r), round(hits[r.id], 4)) for r in rows]
            return sorted(found, key=lambda pair: -pair[1])

        return await self._db.run(load)

    async def recall(
        self, query: str, *, exclude_conversation: str | None = None, k: int = 4
    ) -> list[Recollection]:
        """Échanges d'autres conversations proches de la question."""
        try:
            vector = (await self._embedder.embed([query], "query"))[0]
        except Exception:
            return []
        hits = await self.index.search(vector, k * 3)
        if not hits:
            return []
        scores = dict(hits)

        def load(session: Session) -> list[Recollection]:
            result: list[Recollection] = []
            rows = session.execute(
                select(ChatMessage, Conversation.title)
                .join(Conversation, Conversation.id == ChatMessage.conversation_id)
                .where(ChatMessage.id.in_(list(scores)))
            ).all()
            for message, title in rows:
                if message.conversation_id == exclude_conversation:
                    continue
                reply = session.scalar(
                    select(ChatMessage)
                    .where(
                        ChatMessage.conversation_id == message.conversation_id,
                        ChatMessage.id > message.id,
                        ChatMessage.role == "assistant",
                    )
                    .order_by(ChatMessage.id)
                    .limit(1)
                )
                content = message.content
                if reply is not None:
                    content = f"Toi : {message.content}\nJarvis : {reply.content}"
                result.append(
                    Recollection(
                        replace(MessageRecord.of(message), content=content),
                        str(title),
                        round(scores[message.id], 4),
                    )
                )
            result.sort(key=lambda r: -r.score)
            return result[:k]

        return await self._db.run(load)
