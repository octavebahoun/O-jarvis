"""Ce que Jarvis apprend d'un échange, une fois la réponse donnée : le titre de la
conversation, puis les faits, préférences et liens extraits pour la mémoire.

Avant d'extraire, le Système 1 lit le message :
- « Apprendre ? » : quand il est sûr que non (une question, une tâche, un bavardage), Jarvis ne
  demande rien au modèle ; c'est ce qui le faisait retenir n'importe quoi.
- « Sensible ? » : un secret (mot de passe, PIN, IBAN, clé…) n'est jamais gardé, même mêlé à
  un fait ; un message personnel (santé, argent, adresse…) donne des souvenirs marqués
  « personnel », envoyés au modèle seulement quand la question en parle.

Puis « Mettre à jour un souvenir ? » et « Information incertaine ? » (`ojarvis.systeme1.doutes`)
disent quel souvenir le fait nouveau remplace, lequel reste, et quand une question posée dans la
réponse attend la réponse de l'utilisateur ; une supposition ne devient pas un souvenir.

Après l'extraction, « Combien de temps ? » lit chaque fait : un souvenir de quelques jours
reçoit une échéance (`ojarvis.systeme1.duree`), passé laquelle il s'efface seul.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

from ojarvis.context.engine import ContextEngine, french_now
from ojarvis.conversations.store import ConversationStore, MessageRecord
from ojarvis.core.clock import local_timezone, utc_now
from ojarvis.core.errors import NotFoundError
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
from ojarvis.memory.store import MemoryHit, MemoryStore
from ojarvis.systeme1 import oubli
from ojarvis.systeme1.decideurs import SEUIL_RIEN, Systeme1
from ojarvis.systeme1.doutes import (
    CONFIANCE_SI_INCERTAIN,
    Attente,
    Doute,
    examiner,
    pour_extraction,
)
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
    oublie: list[int] = Field(
        default_factory=list, max_length=12, description="ids des souvenirs à oublier"
    )
    oublie_tout: bool = Field(default=False, description="effacer tout ce qu'on sait de lui")


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
    secret: bool = False
    #: Une demande d'oubli : le modèle d'extraction désigne quoi effacer.
    oublier: bool = False


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
    _context: ContextEngine
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
        attente = self._attente(conversation_id, reply)
        if needs_title:
            await self._title(conversation_id, user, reply)
        if conversation_id in self._context.confirmations:
            # La réponse à « tout effacer ? » : un « oui » franc efface, le reste annule.
            self._context.confirmations.discard(conversation_id)
            if oubli.confirme(user.content):
                await self._tout_oublier()
            return
        if not (self.learn and learn):
            return
        tri = await self._triage(user)
        # La réponse à une question sur un souvenir s'apprend, même brève (« oui », « les deux »).
        if tri.apprendre or tri.oublier or (attente is not None and not tri.secret):
            await self._extract(
                user,
                reply,
                personnel=tri.personnel,
                attente=attente,
                oublier=conversation_id if tri.oublier else None,
            )

    async def _tout_oublier(self) -> None:
        """Ce que Jarvis sait de l'utilisateur, après son « oui » : faits, préférences, profil
        et graphe. La carte de la machine et les impasses du mode shell restent."""
        effaces = 0
        for record in await self._memory.items(limit=100_000):
            if record.kind in {"fait", "preference", "profil"}:
                with contextlib.suppress(NotFoundError):
                    await self._memory.delete(record.id)
                    effaces += 1
        if self._graph is not None:
            entites, _ = await self._graph.graph(limit=100_000)
            for entite in entites:
                with contextlib.suppress(NotFoundError):
                    await self._graph.delete_entity(entite.id)
        log.info("memory_wiped", souvenirs=effaces)

    def _attente(self, conversation_id: str, reply: MessageRecord) -> Attente | None:
        """La question sur un souvenir à laquelle ce message répond, s'il y en a une : le tour
        qui l'a posée y note sa réponse, le suivant (qui y répond) la retire."""
        attente = self._context.attentes.get(conversation_id)
        if attente is None:
            return None
        if not attente.question:
            attente.question = reply.content[-400:]
            return None
        return self._context.attentes.pop(conversation_id)

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
            return Tri(apprendre=False, secret=True)
        rien = (
            apprendre is not None
            and apprendre.reponse == "rien"
            and apprendre.confiance >= SEUIL_RIEN
        )
        personnel = sensible is not None and sensible.reponse == "personnel"
        oublier = oubli.demande_oubli(decisions)
        return Tri(apprendre=not rien, personnel=personnel, oublier=oublier)

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
        self,
        user: MessageRecord,
        reply: MessageRecord,
        *,
        personnel: bool = False,
        attente: Attente | None = None,
        oublier: str | None = None,
    ) -> None:
        """`oublier` : la conversation d'une demande d'oubli (vue par le Système 1)."""
        known = [
            hit
            for hit in await self._memory.search(user.content, k=8)
            if hit.record.kind != "impasse"
        ]
        if attente is not None and attente.souvenir.id not in {h.record.id for h in known}:
            # « oui » ne ressemble à rien : le souvenir en question est joint d'office.
            with contextlib.suppress(NotFoundError):  # effacé entre-temps : plus rien à trancher
                known.append(MemoryHit(await self._memory.get(attente.souvenir.id), 0.0, "attente"))
        known_lines = "\n".join(
            f"- id {h.record.id} [{h.record.kind}] {h.record.content}" for h in known
        )
        names = await self._graph.known_names() if self._graph else []
        entity_lines = ", ".join(f"{n} ({k})" for n, k in names)
        maintenant, fuseau = utc_now(), local_timezone()
        doute = Doute()
        if self._systeme1 is not None:
            floor = self._memory.relevance_floor
            doute = await examiner(self._systeme1, user.content, known, floor)
        lignes = pour_extraction(doute, attente)
        if oublier is not None:
            lignes.append(oubli.CONSIGNE_EXTRACTION)
        tranche = "".join(f"- {ligne}\n" for ligne in lignes)
        prompt = (
            f"{french_now(maintenant, fuseau)}\n\n"
            f"Souvenirs existants proches :\n{known_lines or '(aucun)'}\n\n"
            f"Entités déjà connues : {entity_lines or '(aucune)'}\n\n"
            + (f"À savoir :\n{tranche}\n" if tranche else "")
            + f"Échange :\nUtilisateur : {user.content}\nJarvis : {reply.content}"
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
        if oublier is not None:
            known_ids -= await self._oublier(extraction, known_ids, oublier)
        await self._ranger(
            extraction.faits,
            user=user,
            known_ids=known_ids,
            doute=doute,
            attente=attente,
            personnel=personnel,
        )
        if self._graph is not None and (extraction.entites or extraction.relations):
            await self._graph.remember(
                [(e.nom, e.type, e.description) for e in extraction.entites],
                [(r.source, r.lien, r.cible) for r in extraction.relations],
            )

    async def _oublier(
        self, extraction: Extraction, known_ids: set[int], conversation_id: str
    ) -> set[int]:
        """Efface les souvenirs que le modèle désigne, parmi ceux qu'on lui a montrés ; pour
        « oublie tout », note seulement la question : rien ne part avant le « oui »."""
        vises = set(extraction.oublie) & known_ids
        for item_id in vises:
            with contextlib.suppress(NotFoundError):
                await self._memory.delete(item_id)
        log.info("memory_forgotten", count=len(vises), tout=extraction.oublie_tout)
        if extraction.oublie_tout:
            self._context.confirmations.add(conversation_id)
        return vises

    async def _ranger(
        self,
        faits: list[ExtractedFact],
        *,
        user: MessageRecord,
        known_ids: set[int],
        doute: Doute,
        attente: Attente | None,
        personnel: bool,
    ) -> None:
        """Ajoute ou corrige les faits extraits, selon ce que le Système 1 a tranché."""
        maintenant, fuseau = utc_now(), local_timezone()
        seuil = CONFIANCE_SI_INCERTAIN if doute.incertain else 0.6
        faits = [fact for fact in faits if fact.confiance >= seuil]
        # Le souvenir que le Système 1 garde (sauf si l'utilisateur vient de trancher) ; celui
        # qu'il remplace, par le seul fait du message si le modèle ne l'a pas dit.
        protege = doute.protege
        if attente is not None and attente.souvenir.id == protege:
            protege = None
        if doute.remplace is not None and len(faits) == 1 and faits[0].remplace is None:
            faits[0].remplace = doute.remplace
        if doute.maj is not None:
            log.info("systeme1_mise_a_jour", reponse=doute.maj, incertain=doute.incertain)
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
            remplace = fact.remplace if fact.remplace != protege else None
            if remplace is not None and remplace in known_ids:
                await self._memory.update(
                    remplace,
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
