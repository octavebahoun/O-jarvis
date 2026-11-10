"""Ce que Jarvis apprend d'un échange, une fois la réponse donnée : le titre de la
conversation, puis les faits, préférences et liens extraits pour la mémoire.

Avant d'extraire, le Système 1 lit le message :
- « Apprendre ? » : quand il est sûr que non (une question, une tâche, un bavardage), Jarvis ne
  demande rien au modèle ; c'est ce qui le faisait retenir n'importe quoi.
- « Sensible ? » : un secret (mot de passe, PIN, IBAN, clé…) n'est jamais gardé, même mêlé à
  un fait ; un message personnel (santé, argent, adresse…) donne des souvenirs marqués
  « personnel », envoyés au modèle seulement quand la question en parle.

Après l'extraction, « Combien de temps ? » lit chaque fait : un souvenir de quelques jours
reçoit une échéance (`ojarvis.systeme1.duree`), passé laquelle il s'efface seul.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

from ojarvis.context.engine import french_now
from ojarvis.conversations.store import ConversationStore, MessageRecord
from ojarvis.core.clock import local_timezone, utc_now
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
from ojarvis.systeme1.decideurs import Systeme1
from ojarvis.systeme1.duree import echeance, jour_dit

log = get_logger(__name__)


class ExtractedFact(BaseModel):
    type: Literal["fait", "preference", "profil"]
    contenu: str = Field(min_length=3, max_length=400)
    confiance: float = Field(ge=0, le=1)
    remplace: int | None = Field(default=None, description="id d'un souvenir que ce fait corrige")
    date: str | None = Field(
        default=None, max_length=10, description="AAAA-MM-JJ : le jour dont parle un fait daté"
    )


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
    "Un fait daté (rendez-vous, examen, voyage, fin d'une situation : « jusqu'à jeudi ») "
    "porte ce jour dans « date » (AAAA-MM-JJ), calculé depuis la date d'aujourd'hui donnée "
    "plus bas ; pas de « date » pour une naissance ni pour un passé lointain. Dans le fait, "
    "écris le jour en entier (« le jeudi 12 novembre 2026 »), jamais « jeudi » ou « demain » "
    "seuls. "
    "S'il n'y a rien à retenir, renvoie des listes vides."
)

TITLE_PROMPT = (
    "Donne un titre de trois à six mots, dans la langue de la conversation, sans guillemets "
    "ni point final, pour la conversation suivante."
)

#: Au-dessus de cette confiance, un « rien » du Système 1 évite l'extraction. Mesuré le
#: 10/11/2026 sur les phrases de test : à 0,5, 13 « rien » sur 21 évités, aucun fait perdu.
SEUIL_RIEN = 0.5

_TITLE_TRIM = " \t\n.!?:;«»\"'“”‘’"


def clean_title(raw: str) -> str:
    """Retire guillemets, ponctuation finale et espaces, dans n'importe quel ordre."""
    first_line = raw.strip().splitlines()[0] if raw.strip() else ""
    return " ".join(first_line.strip(_TITLE_TRIM).split())[:80]


@dataclass(frozen=True)
class Tri:
    """Ce que le Système 1 dit d'un message, pour la mémoire."""

    apprendre: bool = True
    personnel: bool = False


class ChatLearning:
    """Après la réponse : titrer la conversation, puis apprendre de l'échange (mélangée à
    `ChatService`)."""

    _conversations: ConversationStore
    _memory: MemoryStore
    _graph: GraphStore | None
    _router: ModelRouter
    _bus: EventBus
    _background: set[asyncio.Task[None]]
    _systeme1: Systeme1 | None
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
        if not (self.learn and learn):
            return
        tri = await self._triage(user)
        if tri.apprendre:
            await self._extract(user, reply, personnel=tri.personnel)

    async def _triage(self, user: MessageRecord) -> Tri:
        """Sans Système 1, ou s'il se tait, le modèle décide seul, comme avant."""
        decisions = await self._systeme1.lire(user.content) if self._systeme1 else None
        if not decisions:
            return Tri()
        apprendre = decisions.get("apprendre")
        sensible = decisions.get("sensible")
        log.info(
            "systeme1_memoire",
            apprendre=apprendre and (apprendre.reponse, round(apprendre.confiance, 2)),
            sensible=sensible and (sensible.reponse, round(sensible.confiance, 2)),
        )
        if sensible is not None and sensible.reponse == "secret":
            # Même incertain : rater un fait coûte peu, garder un mot de passe coûte cher.
            # Mesuré le 10/11/2026 : les 20 secrets du test pris, 3 faux sur 62.
            return Tri(apprendre=False)
        rien = (
            apprendre is not None
            and apprendre.reponse == "rien"
            and apprendre.confiance >= SEUIL_RIEN
        )
        personnel = sensible is not None and sensible.reponse == "personnel"
        return Tri(apprendre=not rien, personnel=personnel)

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

    async def _extract(
        self, user: MessageRecord, reply: MessageRecord, *, personnel: bool = False
    ) -> None:
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
        maintenant, fuseau = utc_now(), local_timezone()
        prompt = (
            f"{french_now(maintenant, fuseau)}\n\n"
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
        faits = [fact for fact in extraction.faits if fact.confiance >= 0.6]
        textes = [f.contenu for f in faits]
        durees = await self._systeme1.decider("duree", textes) if self._systeme1 else None
        for i, fact in enumerate(faits):
            duree = durees[i] if durees else None
            fin = echeance(duree, jour_dit(fact.date), maintenant, fuseau)
            if duree is not None:
                log.info(
                    "systeme1_duree",
                    reponse=duree.reponse,
                    confiance=round(duree.confiance, 2),
                    efface=fin.expires_at is not None,
                )
            if fact.remplace is not None and fact.remplace in known_ids:
                await self._memory.update(
                    fact.remplace,
                    content=fact.contenu,
                    kind=fact.type,
                    personnel=personnel or None,  # une correction ne retire pas la marque
                    echeance=fin,
                )
            else:
                await self._memory.add(
                    fact.type,
                    fact.contenu,
                    source="conversation",
                    source_message_id=user.id,
                    confidence=fact.confiance,
                    personnel=personnel,
                    echeance=fin,
                )
        if self._graph is not None and (extraction.entites or extraction.relations):
            await self._graph.remember(
                [(e.nom, e.type, e.description) for e in extraction.entites],
                [(r.source, r.lien, r.cible) for r in extraction.relations],
            )
