"""Fournisseur de test déterministe : réponses scénarisées, latence simulée, pannes à la demande."""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field

from ojarvis.llm.errors import LLMError
from ojarvis.llm.types import (
    LLMRequest,
    ReasoningDelta,
    StreamEnd,
    StreamEvent,
    TextDelta,
    ToolCall,
    ToolCallReady,
    ToolCallStarted,
    Usage,
)


@dataclass
class FakeReply:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    reasoning: str = ""
    #: Erreur levée avant le premier morceau (repli possible) ou après (`fail_after_text`).
    error: LLMError | None = None
    fail_after_text: bool = False


Script = Callable[[LLMRequest], FakeReply]


def echo_script(request: LLMRequest) -> FakeReply:
    last = next((m.content for m in reversed(request.messages) if m.role == "user"), "")
    return FakeReply(text=f"Réponse de test : {last}")


class FakeProvider:
    local = False

    def __init__(
        self,
        name: str = "fake",
        script: Script = echo_script,
        *,
        delay_s: float = 0.0,
        chunk_chars: int = 12,
        configured: bool = True,
        local: bool = False,
    ) -> None:
        self.name = name
        self.local = local
        self._script = script
        self._delay = delay_s
        self._chunk = chunk_chars
        self._configured = configured
        self.requests: list[tuple[LLMRequest, str]] = []

    def is_configured(self) -> bool:
        return self._configured

    async def stream(self, request: LLMRequest, model: str) -> AsyncIterator[StreamEvent]:
        self.requests.append((request, model))
        reply = self._script(request)
        if reply.error is not None and not reply.fail_after_text:
            raise reply.error
        if self._delay:
            await asyncio.sleep(self._delay)
        if reply.reasoning:
            yield ReasoningDelta(reply.reasoning)
        for i in range(0, len(reply.text), self._chunk):
            yield TextDelta(reply.text[i : i + self._chunk])
            if self._delay:
                await asyncio.sleep(self._delay / 10)
        if reply.error is not None:
            raise reply.error
        for call in reply.tool_calls:
            yield ToolCallStarted(call.name)  # comme un vrai flux : le nom, puis l'appel
            yield ToolCallReady(call)
        words = sum(len(m.content.split()) for m in request.messages)
        yield StreamEnd(
            Usage(input_tokens=words * 2, output_tokens=max(1, len(reply.text.split()) * 2)),
            "tool_calls" if reply.tool_calls else "stop",
        )

    async def list_models(self) -> list[str]:
        return ["fake-model"]


_SHOP = re.compile(r"https?://127\.0\.0\.1:\d+/boutique")
_PRICE = re.compile(r"\d+,\d{2} €")


def _shop_agent(request: LLMRequest, shop: str) -> FakeReply:
    """Ouvre la boutique (lecture, sans validation), puis propose un plan d'actions."""
    rounds = sum(1 for m in request.messages if m.role == "assistant" and m.tool_calls)
    if rounds == 0:
        return FakeReply(
            text="J'ouvre la boutique.",
            tool_calls=[ToolCall("e2e-ouvrir", "navigateur_ouvrir", {"url": shop})],
        )
    if rounds == 1:
        steps = [
            {
                "outil": "navigateur_remplir",
                "arguments": {"champ": "Recherche", "valeur": "thé vert"},
                "pourquoi": "Taper le produit dans la recherche de la boutique.",
            },
            {
                "outil": "navigateur_cliquer",
                "arguments": {"element": "Chercher"},
                "pourquoi": "Lancer la recherche.",
            },
            {
                "outil": "navigateur_lire",
                "arguments": {"quoi": "texte"},
                "pourquoi": "Lire le prix dans les résultats.",
            },
        ]
        plan = {"titre": "Chercher le thé vert sur la boutique", "etapes": steps}
        return FakeReply(tool_calls=[ToolCall("e2e-plan", "proposer_un_plan", plan)])
    return FakeReply(text="J'ai préparé un plan en trois étapes : il attend ta validation.")


def e2e_script(request: LLMRequest) -> FakeReply:
    """Réponses prévisibles pour les tests de bout en bout (mode développement uniquement)."""
    if request.tools and request.tools[0].name == "repondre":
        return FakeReply(tool_calls=[ToolCall("e2e", "repondre", {"faits": []})])
    system = request.messages[0].content if request.messages else ""
    last = next((m.content for m in reversed(request.messages) if m.role == "user"), "")
    if "titre" in system:
        return FakeReply(
            text="Courses à la boutique" if _SHOP.search(last) else "Conversation de test"
        )
    if "briefing du matin" in system:
        return FakeReply(text="Bonjour ! Briefing de test : rien d'urgent aujourd'hui.")
    if "Tu viens d'exécuter un plan" in system:
        price = _PRICE.search(last)
        return FakeReply(
            text=f"C'est fait : le thé vert coûte {price.group(0)}." if price else "C'est fait."
        )
    shop = _SHOP.search(last)
    if shop and request.tools:
        return _shop_agent(request, shop.group(0))
    return FakeReply(text=f"Bien reçu : **{last}**. Je suis la réponse de test de Jarvis.")
