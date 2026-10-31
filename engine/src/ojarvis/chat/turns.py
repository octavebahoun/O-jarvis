"""Un tour de conversation, par morceaux : l'état du tour et de chaque appel au modèle, les
redites retenues au début d'un nouveau tour, et les petits outils autour (événements de
contexte, marques des plans revenus).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ojarvis.agent.reports import PlanReport
from ojarvis.agent.rules import PLAN_MISSING
from ojarvis.agent.turn import AgentTurn
from ojarvis.chrome.page import BrowserPage
from ojarvis.context.engine import BuiltContext
from ojarvis.conversations.store import MessageRecord
from ojarvis.llm.policy import Relay, Route
from ojarvis.llm.router import RoutedStream
from ojarvis.llm.types import (
    LLMRequest,
    Message,
    RouteInfo,
    TaskType,
    ToolCall,
    Usage,
)

#: La fin d'une phrase : sa ponctuation (mise en forme comprise), puis un blanc.
_SENTENCE_END = re.compile(r"[.!?…:][*_`]*\s+")


def _plain(sentence: str) -> str:
    """Une phrase sans mise en forme, casse ni ponctuation finale : pour reconnaître une
    répétition."""
    return " ".join(re.sub(r"[*_`]", "", sentence).split()).lower().rstrip(".!?…: ")


def _sentences(text: str) -> set[str]:
    return {plain for part in _SENTENCE_END.split(text) if (plain := _plain(part))}


@dataclass
class _Round:
    """Un appel au modèle dans un tour : ce qu'il a dit, pensé et demandé."""

    said: list[str] = field(default_factory=list)
    reasoning: list[str] = field(default_factory=list)
    calls: list[ToolCall] = field(default_factory=list)
    #: Après des outils, le début du texte est retenu le temps de vérifier qu'il ne répète
    #: pas une phrase déjà dite dans ce tour (le 30/10, Muse Spark redisait « Je vais cliquer
    #: sur Projets, puis sur Blog » après avoir proposé le plan).
    checking: bool = False
    held: str = ""
    known: set[str] = field(default_factory=set)

    def unrepeated(self, *, final: bool) -> str:
        """Le texte retenu, moins ses premières phrases déjà dites ; « » tant qu'une phrase
        n'est pas finie (`final` : le modèle a fini d'écrire)."""
        while self.checking:
            held = self.held.lstrip()
            end = _SENTENCE_END.search(held)
            if end is None and not final:
                return ""
            sentence = held[: end.end()] if end else held
            if _plain(sentence) and _plain(sentence) in self.known:
                self.held = held[len(sentence) :]
                continue
            self.checking = False
        text, self.held = self.held, ""
        return text


@dataclass
class _Turn:
    """État d'un tour en cours (réponse partielle, route, jetons, chronomètre)."""

    assistant: MessageRecord
    start: float
    parts: list[str] = field(default_factory=list)
    route: RouteInfo | None = None
    usage: Usage = field(default_factory=Usage)
    state: RoutedStream = field(default_factory=RoutedStream)
    first_token_ms: float | None = None
    trace_id: str = ""
    finished: bool = False
    agent: AgentTurn = field(default_factory=AgentTurn)
    #: Un modèle plus fort pour ce tour, après des échecs répétés (profil « Tout Meta »).
    relay: Relay | None = None


TRACE_TITLES = {"voice": "Tour vocal", "shell": "Tour du mode shell", "chrome": "Tour dans Chrome"}

#: Le relais réglé : (modèle de relais, échecs de la même commande avant de l'appeler), ou
#: None quand il est coupé ou hors du profil « Tout Meta ».
RelayPolicy = Callable[[], tuple[Route, int] | None]

TASKS_BY_CHANNEL: dict[str, TaskType] = {"voice": "voice", "shell": "reasoning"}


def _remind_plan(request: LLMRequest, said: str) -> None:
    """Le modèle a annoncé un plan sans le proposer : il est relancé (relance non gardée)."""
    if said:  # un message vide, sans appel d'outil, serait refusé par l'API
        request.messages.append(Message("assistant", said))
    request.messages.append(Message("user", PLAN_MISSING))


def _keep_marks(turn: AgentTurn, reports: list[PlanReport]) -> None:
    """Les sorties revenues d'un plan restent des données pour toute la suite : la garde des
    adresses inventées, le signalement des plans et l'arrêt de « toujours » tiennent."""
    for report in reports:
        turn.tainted = turn.tainted or report.tainted
        turn.web = turn.web or report.web
        turn.known_urls |= report.urls


def _add_to_last_user_message(request: LLMRequest, text: str) -> None:
    """Ajoute un bloc (résultats d'un plan) à la demande de l'utilisateur, sans un second
    message « user » que certains fournisseurs refusent."""
    last = request.messages[-1]
    if last.role == "user":
        request.messages[-1] = Message("user", f"{last.content}\n\n{text}")
    else:
        request.messages.append(Message("user", text))


def _context_event(context: BuiltContext) -> dict[str, Any]:
    return {
        "budget": context.budget,
        "used": context.used,
        "items": [
            {"kind": i.kind, "preview": i.text[:90], "kept": i.kept}
            for i in context.items
            if i.kind != "systeme"
        ],
    }


def _add_page(request: LLMRequest, turn: AgentTurn, page: BrowserPage | None) -> None:
    """Chrome : l'onglet ouvert rejoint la demande, comme une donnée lue à l'extérieur."""
    if page is not None:
        _add_to_last_user_message(request, page.block())
        page.mark(turn)


def _meta(turn: _Turn) -> dict[str, Any] | None:
    """Outils utilisés et plans proposés : l'interface les affiche sous la réponse."""
    if not turn.agent.tools and not turn.agent.plans:
        return None
    return {"tools": turn.agent.tools, "plans": turn.agent.plans}
