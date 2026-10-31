"""Ce que Jarvis apprend d'un échange, une fois la réponse donnée : le titre de la
conversation, puis les faits, préférences et liens extraits pour la mémoire.
"""

from __future__ import annotations

import asyncio
from typing import Literal

from pydantic import BaseModel, Field

from ojarvis.conversations.store import ConversationStore, MessageRecord
from ojarvis.core.logs import get_logger
from ojarvis.events.bus import EventBus
from ojarvis.llm.errors import LLMError
from ojarvis.llm.router import ModelRouter
from ojarvis.llm.structured import generate_structured
from ojarvis.llm.types import (
    LLMRequest,
    Message,
)
from ojarvis.memory.graph import GraphStore
from ojarvis.memory.store import MemoryStore

log = get_logger(__name__)


class ExtractedFact(BaseModel):
    type: Literal["fait", "preference", "profil"]
    contenu: str = Field(min_length=3, max_length=400)
    confiance: float = Field(ge=0, le=1)
    remplace: int | None = Field(default=None, description="id d'un souvenir que ce fait corrige")


class ExtractedEntity(BaseModel):
    nom: str = Field(min_length=1, max_length=120)
    type: Literal["moi", "personne", "projet", "evenement", "lieu", "organisation", "sujet"]
    description: str | None = Field(default=None, max_length=300)


class ExtractedRelation(BaseModel):
    source: str = Field(min_length=1, max_length=120)
    lien: str = Field(min_length=2, max_length=80)
    cible: str = Field(min_length=1, max_length=120)


class Extraction(BaseModel):
    faits: list[ExtractedFact] = Field(default_factory=list, max_length=8)
    entites: list[ExtractedEntity] = Field(default_factory=list, max_length=10)
    relations: list[ExtractedRelation] = Field(default_factory=list, max_length=10)


EXTRACTION_PROMPT = (
    "Tu tiens à jour la mémoire de Jarvis sur l'utilisateur. Lis l'échange ci-dessous et "
    "relève seulement les informations durables que l'utilisateur donne sur lui-même : "
    "identité, goûts, habitudes, projets, proches, contraintes. Ignore le temporaire (météo du "
    "jour, humeur passagère), les suppositions et les connaissances générales. Chaque fait est "
    "une phrase autonome à la troisième personne ; désigne l'utilisateur par son prénom s'il "
    "est connu, sinon « L'utilisateur ». « profil » = identité stable (nom, ville, métier) ; "
    "« preference » = goût ou façon de faire ; « fait » = le reste. Si un fait corrige un "
    "souvenir existant, mets son id dans « remplace ». Un prénom ne se retient que s'il est "
    "dit clairement (« je m'appelle… ») ; une phrase confuse ou incomplète ne donne rien. "
    "Relève aussi, pour la mémoire graphe, les entités durables de sa vie (personnes, projets, "
    "événements, lieux, organisations, sujets qu'il étudie) et les relations entre elles : "
    "l'utilisateur est l'entité « moi » (type moi) ; une relation est courte, au présent "
    "(« travaille sur », « connaît », « habite à », « participe à », « apprend ») et relie deux "
    "entités de la liste. Réutilise exactement les noms déjà connus. "
    "S'il n'y a rien à retenir, renvoie des listes vides."
)

TITLE_PROMPT = (
    "Donne un titre de trois à six mots, dans la langue de la conversation, sans guillemets "
    "ni point final, pour la conversation suivante."
)

_TITLE_TRIM = " \t\n.!?:;«»\"'“”‘’"


def clean_title(raw: str) -> str:
    """Retire guillemets, ponctuation finale et espaces, dans n'importe quel ordre."""
    first_line = raw.strip().splitlines()[0] if raw.strip() else ""
    return " ".join(first_line.strip(_TITLE_TRIM).split())[:80]


class ChatLearning:
    """Après la réponse : titrer la conversation, puis apprendre de l'échange (mélangée à
    `ChatService`)."""

    _conversations: ConversationStore
    _memory: MemoryStore
    _graph: GraphStore | None
    _router: ModelRouter
    _bus: EventBus
    _background: set[asyncio.Task[None]]
    learn: bool

    def _after(
        self,
        conversation_id: str,
        needs_title: bool,
        user: MessageRecord,
        reply: MessageRecord,
        *,
        learn: bool = True,
    ) -> None:
        task = asyncio.create_task(
            self._learn_from(conversation_id, needs_title, user, reply, learn=learn)
        )
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def _learn_from(
        self,
        conversation_id: str,
        needs_title: bool,
        user: MessageRecord,
        reply: MessageRecord,
        *,
        learn: bool = True,
    ) -> None:
        await self._conversations.index_exchange(user, reply)
        if needs_title:
            await self._title(conversation_id, user, reply)
        if self.learn and learn:
            await self._extract(user, reply)

    async def _title(self, conversation_id: str, user: MessageRecord, reply: MessageRecord) -> None:
        try:
            result = await self._router.complete(
                "title",
                LLMRequest(
                    messages=[
                        Message("system", TITLE_PROMPT),
                        Message(
                            "user", f"Utilisateur : {user.content}\nJarvis : {reply.content}"[:3000]
                        ),
                    ],
                    max_tokens=30,
                ),
            )
        except LLMError:
            log.warning("title_generation_failed")
            return
        title = clean_title(result.text)
        if title:
            await self._conversations.rename(conversation_id, title[:80])
            await self._bus.publish("conversation.updated", {"id": conversation_id})

    async def _extract(self, user: MessageRecord, reply: MessageRecord) -> None:
        known = [
            hit
            for hit in await self._memory.search(user.content, k=8)
            if hit.record.kind != "impasse"
        ]
        known_lines = "\n".join(
            f"- id {h.record.id} [{h.record.kind}] {h.record.content}" for h in known
        )
        names = await self._graph.known_names() if self._graph else []
        entity_lines = ", ".join(f"{n} ({k})" for n, k in names)
        prompt = (
            f"Souvenirs existants proches :\n{known_lines or '(aucun)'}\n\n"
            f"Entités déjà connues : {entity_lines or '(aucune)'}\n\n"
            f"Échange :\nUtilisateur : {user.content}\nJarvis : {reply.content}"
        )[:6000]
        try:
            extraction = await generate_structured(
                self._router,
                "extract",
                [Message("system", EXTRACTION_PROMPT), Message("user", prompt)],
                Extraction,
            )
        except LLMError:
            log.warning("memory_extraction_failed")
            return
        known_ids = {h.record.id for h in known}
        for fact in extraction.faits:
            if fact.confiance < 0.6:
                continue
            if fact.remplace is not None and fact.remplace in known_ids:
                await self._memory.update(fact.remplace, content=fact.contenu, kind=fact.type)
            else:
                await self._memory.add(
                    fact.type,
                    fact.contenu,
                    source="conversation",
                    source_message_id=user.id,
                    confidence=fact.confiance,
                )
        if self._graph is not None and (extraction.entites or extraction.relations):
            await self._graph.remember(
                [(e.nom, e.type, e.description) for e in extraction.entites],
                [(r.source, r.lien, r.cible) for r in extraction.relations],
            )
