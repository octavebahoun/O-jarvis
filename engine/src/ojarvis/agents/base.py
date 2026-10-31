"""Le socle commun des agents : un objectif, des outils et un contexte propres.

Un agent ne voit pas ta conversation : seulement la tâche qu'on lui confie. Il n'utilise que
ses outils, toujours par la porte des permissions : il lit seul ; une action devient un plan
qui attend ta validation, comme pour Jarvis lui-même. Il rend un compte rendu.
"""

from __future__ import annotations

import time
from abc import ABC
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import ClassVar

from pydantic import ValidationError

from ojarvis.agent.loop import Agent
from ojarvis.agent.plans import PLAN_TOOL, PlanIn
from ojarvis.agent.reports import urls_in
from ojarvis.agent.turn import AgentTurn
from ojarvis.context.engine import PERSONA
from ojarvis.llm.router import ModelRouter
from ojarvis.llm.types import LLMRequest, Message, ToolCall, ToolSpec
from ojarvis.tools.guards import DATA_RULE

MAX_REPORT = 6_000


@dataclass(frozen=True)
class AgentStep:
    tool: str
    description: str
    status: str  # done | failed | proposed
    error: str | None = None


@dataclass(frozen=True, slots=True)
class MissionBudget:
    """Les limites d'une mission (#15) : au-delà, elle est suspendue et tu es prévenu. Le
    nombre d'étapes, lui, est celui de l'agent (`max_steps`)."""

    dollars: float = 0.50
    seconds: float = 600.0
    tokens: int = 300_000
    #: Échecs d'outil d'affilée : une piste qui ne mène nulle part.
    failures: int = 3


@dataclass
class AgentOutcome:
    report: str
    steps: list[AgentStep] = field(default_factory=list)
    plans: list[str] = field(default_factory=list)
    #: L'agent a lu un contenu extérieur : son compte rendu est une donnée, pas une consigne.
    external: bool = False
    #: Pourquoi la mission s'est arrêtée avant la fin (un budget atteint, des échecs répétés).
    suspended: str | None = None
    cost_usd: float = 0.0
    tokens: int = 0


def _money(dollars: float) -> str:
    return f"{dollars:.2f} $".replace(".", ",")


Progress = Callable[[AgentStep], Awaitable[None]]


class BaseAgent(ABC):
    """Tous les agents en héritent ; seuls changent l'objectif, les outils et les limites."""

    name: ClassVar[str]
    label: ClassVar[str]
    goal: ClassVar[str]
    tools: ClassVar[tuple[str, ...]]
    max_steps: ClassVar[int] = 6

    def __init__(self, *, router: ModelRouter, core: Agent) -> None:
        self._router = router
        self._core = core

    def instructions(self) -> str:
        return (
            f"{PERSONA}\n\nTu es l'agent « {self.label} » de Jarvis. Ton objectif : {self.goal}\n\n"
            "Tu travailles seul sur la tâche confiée, avec tes seuls outils. Lire : fais-le "
            "directement. Agir (écrire, envoyer, créer) : jamais seul, propose un plan que "
            "l'utilisateur validera. Quand tu as fini, rends un compte rendu bref et précis, en "
            f"français, avec tes sources s'il y en a.\n\n{DATA_RULE}"
        )

    def specs(self) -> list[ToolSpec]:
        allowed = {*self.tools, PLAN_TOOL}
        return [s for s in self._core.specs() if s.name in allowed]

    def _outside(self, call: ToolCall) -> str | None:
        """Un outil, ou une étape de plan, hors de ses attributions."""
        if call.name == PLAN_TOOL:
            try:
                plan = PlanIn.model_validate(call.arguments)
            except ValidationError:
                return None  # la porte expliquera l'erreur
            stray = [s.outil for s in plan.etapes if s.outil not in self.tools]
            return stray[0] if stray else None
        return None if call.name in self.tools else call.name

    async def run(
        self,
        task: str,
        *,
        conversation_id: str | None = None,
        progress: Progress | None = None,
        budget: MissionBudget | None = None,
    ) -> AgentOutcome:
        budget = budget or MissionBudget()
        started = time.monotonic()
        failures = 0
        messages = [Message("system", self.instructions()), Message("user", task)]
        turn = AgentTurn()
        turn.known_urls |= urls_in(task)
        outcome = AgentOutcome(report="")
        for _ in range(self.max_steps):
            result = await self._router.complete(
                "chat", LLMRequest(messages=list(messages), tools=self.specs())
            )
            outcome.cost_usd += result.cost_usd
            outcome.tokens += result.usage.input_tokens + result.usage.output_tokens
            if not result.tool_calls:
                outcome.report = result.text.strip()[:MAX_REPORT]
                break
            messages.append(result.as_message())
            for call in result.tool_calls:
                stray = self._outside(call)
                if stray is not None:
                    step = AgentStep(
                        call.name, call.name, "failed", f"{stray} : hors de mes outils"
                    )
                    content, is_error = f"Échec : l'outil {stray} n'est pas dans tes outils.", True
                else:
                    done = await self._core.handle(
                        call, turn, conversation_id=conversation_id, request=task
                    )
                    event = done.event
                    step = AgentStep(
                        call.name,
                        str(event.get("description") or call.name),
                        str(event.get("status") or "done"),
                        event.get("error"),
                    )
                    content, is_error = done.content, done.is_error
                outcome.steps.append(step)
                failures = failures + 1 if step.status == "failed" else 0
                if progress is not None:
                    await progress(step)
                messages.append(Message("tool", content, tool_call_id=call.id, is_error=is_error))
            reason = _over(budget, outcome, time.monotonic() - started, failures)
            if reason is not None:
                # Suspendue : plus aucun appel au modèle, le compte rendu dit où elle en est.
                outcome.suspended = reason
                outcome.report = _suspended_report(reason, outcome)
                break
        else:
            # Nombre d'étapes atteint : il rend ce qu'il a, sans nouvel outil.
            messages.append(
                Message("user", "Tu as atteint ton nombre d'étapes : rends ton compte rendu.")
            )
            result = await self._router.complete("chat", LLMRequest(messages=messages))
            outcome.report = result.text.strip()[:MAX_REPORT]
        outcome.plans = list(turn.plans)
        outcome.external = turn.tainted
        return outcome


def _over(
    budget: MissionBudget, outcome: AgentOutcome, elapsed: float, failures: int
) -> str | None:
    """La limite dépassée, s'il y en a une."""
    if outcome.cost_usd >= budget.dollars:
        return f"budget de {_money(budget.dollars)} atteint ({_money(outcome.cost_usd)})"
    if outcome.tokens >= budget.tokens:
        return f"budget de {budget.tokens} jetons atteint ({outcome.tokens})"
    if elapsed >= budget.seconds:
        return f"durée de {budget.seconds / 60:.0f} min atteinte"
    if failures >= budget.failures:
        return f"{failures} échecs d'affilée"
    return None


def _suspended_report(reason: str, outcome: AgentOutcome) -> str:
    done = [step.description for step in outcome.steps if step.status == "done"]
    lines = [f"Mission suspendue : {reason}."]
    if done:
        lines.append("Déjà fait : " + " ; ".join(done[-5:]) + ".")
    lines.append("Relance-la pour qu'elle reprenne.")
    return "\n".join(lines)
