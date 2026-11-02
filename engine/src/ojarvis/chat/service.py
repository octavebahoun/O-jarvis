"""Un tour de conversation, du message de l'utilisateur à la réponse diffusée en direct.

Utilisé par l'interface (texte), la voix et Telegram. Après la réponse, sans faire
attendre l'utilisateur : titre de la conversation, vectorisation de l'échange et
extraction des faits durables (visibles et modifiables dans l'écran Mémoire).
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections import Counter
from collections.abc import AsyncGenerator, AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal

from ojarvis.agent.loop import MAX_TOOL_ROUNDS, Agent
from ojarvis.agent.reports import PlanReport, RanCommand, plan_summary, urls_in
from ojarvis.agent.rules import ROUNDS_EXHAUSTED, claims_missing_plan
from ojarvis.agent.turn import AgentTurn
from ojarvis.chat.learning import ChatLearning
from ojarvis.chat.turns import (
    TASKS_BY_CHANNEL,
    TRACE_TITLES,
    RelayPolicy,
    _add_page,
    _add_to_last_user_message,
    _context_event,
    _keep_marks,
    _meta,
    _remind_plan,
    _Round,
    _sentences,
    _Turn,
)
from ojarvis.chrome.page import BrowserPage
from ojarvis.context.engine import ContextEngine
from ojarvis.conversations.store import DEFAULT_TITLE, ConversationStore, MessageRecord
from ojarvis.core.errors import InvalidInputError
from ojarvis.core.logs import get_logger
from ojarvis.events.bus import EventBus
from ojarvis.extension import MachineMap
from ojarvis.llm.errors import LLMError
from ojarvis.llm.policy import Relay
from ojarvis.llm.router import ModelRouter
from ojarvis.llm.types import (
    LLMRequest,
    Message,
    ReasoningDelta,
    RouteInfo,
    StreamEnd,
    TaskType,
    TextDelta,
    ToolCall,
    ToolCallReady,
    Usage,
)
from ojarvis.memory.graph import GraphStore
from ojarvis.memory.store import MemoryStore
from ojarvis.security.redaction import REDACTOR
from ojarvis.tracing.tracer import Tracer

log = get_logger(__name__)

MAX_INPUT_CHARS = 8000
Channel = Literal["app", "voice", "telegram", "automation", "shell", "chrome"]
#: En mode shell, le modèle enchaîne plus de commandes dans une réponse (lire, corriger,
#: relancer) ; les actions, elles, attendent toujours ta validation.
SHELL_TOOL_ROUNDS = 16
#: Canaux où tu n'es pas devant ton PC : les outils de Chrome n'y sont pas proposés.
AWAY_CHANNELS = frozenset({"telegram", "automation"})


@dataclass(frozen=True, slots=True)
class ChatEvent:
    type: str  # start | context | route | delta | tool | plan | done | error
    data: dict[str, Any] = field(default_factory=dict)


class ChatService(ChatLearning):
    def __init__(
        self,
        *,
        conversations: ConversationStore,
        memory: MemoryStore,
        context: ContextEngine,
        router: ModelRouter,
        tracer: Tracer,
        bus: EventBus,
        learn: bool = True,
        agent: Agent | None = None,
        graph: GraphStore | None = None,
        relay: RelayPolicy | None = None,
        machine: MachineMap | None = None,
    ) -> None:
        self._conversations = conversations
        self._memory = memory
        self._graph = graph
        self._context = context
        self._router = router
        self._tracer = tracer
        self._bus = bus
        self.learn = learn
        self._agent = agent
        self._relay_policy: RelayPolicy = relay or (lambda: None)
        self._machine = machine
        #: Par conversation, les commandes en échec et leur nombre d'échecs d'affilée.
        self._failures: dict[str, Counter[str]] = {}
        self._background: set[asyncio.Task[None]] = set()

    async def respond(
        self,
        *,
        text: str,
        conversation_id: str | None = None,
        channel: Channel = "app",
        learn: bool = True,
        shell_folder: str | None = None,
        after_plan: list[str] | None = None,
        ran: RanCommand | None = None,
        page: BrowserPage | None = None,
    ) -> AsyncGenerator[ChatEvent]:
        """Un tour de conversation, en flux. `learn=False` : rien n'est retenu de ce tour
        (par exemple une transcription vocale douteuse).

        Mode shell : `shell_folder` est le dossier de la session ; `after_plan`, les plans
        validés dans le terminal, dont les résultats reviennent au modèle pour qu'il continue ;
        `ran`, une commande sudo qu'il a proposée et que l'utilisateur a lancée lui-même.
        Chrome : `page`, l'onglet ouvert, donné au modèle comme une donnée.
        """
        if (after_plan or ran is not None) and conversation_id is None:
            raise InvalidInputError("La suite d'un plan va avec sa conversation.")
        if (channel == "shell") != (shell_folder is not None):
            raise InvalidInputError(
                "Le dossier de travail va avec le mode shell, et seulement lui."
            )
        # Une clé collée par erreur n'est ni enregistrée en clair ni envoyée au modèle.
        cleaned = REDACTOR.redact(text.strip())
        if not cleaned or len(cleaned) > MAX_INPUT_CHARS:
            raise InvalidInputError("Le message doit faire entre 1 et 8000 caractères.")
        if conversation_id:
            conversation = await self._conversations.get(conversation_id)
        else:
            conversation = await self._conversations.create(channel)
            await self._bus.publish(
                "conversation.created", {"id": conversation.id, "channel": channel}
            )
        # Les résultats d'un plan validé : lus avant tout enregistrement, pour qu'un plan
        # d'une autre conversation soit refusé sans laisser de message derrière lui.
        reports = await self._reports(conversation.id, after_plan, ran)
        history = await self._conversations.messages(conversation.id, limit=60)
        user = await self._conversations.add_message(conversation.id, "user", cleaned)
        assistant = await self._conversations.add_message(
            conversation.id, "assistant", "", status="streaming"
        )
        turn = _Turn(
            assistant=assistant,
            start=time.perf_counter(),
            agent=AgentTurn(away=channel in AWAY_CHANNELS),
        )
        self._after_plans(turn, conversation.id, reports)
        # La suite d'un plan n'a pour texte que « Continue. » : le relecteur et les plans
        # gardent la demande d'origine.
        request_text = next((r.request for r in reports if r.request), cleaned)
        yield ChatEvent(
            "start",
            {
                "conversation_id": conversation.id,
                "user_message_id": user.id,
                "assistant_message_id": assistant.id,
            },
        )
        try:
            async with self._tracer.trace(
                TRACE_TITLES.get(channel, "Tour de conversation"),
                kind="voice" if channel == "voice" else "chat",
                meta={"conversation_id": conversation.id, "channel": channel},
                reuse=True,
            ) as trace:
                turn.trace_id = trace.id
                async for event in self._machine_card(channel):
                    yield event
                async with self._tracer.span("contexte", kind="context") as span:
                    context = await self._context.build(
                        conversation_id=conversation.id,
                        user_text=cleaned,
                        channel=channel,
                        history=history,
                    )
                    span.set(**context.summary())
                yield ChatEvent("context", _context_event(context))
                request = LLMRequest(
                    messages=context.messages, max_tokens=500 if channel == "voice" else 4000
                )
                if reports:
                    _add_to_last_user_message(request, "\n\n".join(r.text for r in reports))
                _add_page(request, turn.agent, page)
                # Mode shell : on y écrit du code, la réflexion paie (effort « high » chez
                # Meta : environ 2 s de plus à l'essai, pour un raisonnement six fois plus long).
                task: TaskType = TASKS_BY_CHANNEL.get(channel, "chat")
                try:
                    async for event in self._reply(
                        task,
                        request,
                        turn,
                        conversation_id=conversation.id,
                        user_text=request_text,
                        shell_folder=shell_folder,
                    ):
                        yield event
                except LLMError as error:
                    trace.fail(error.public)
                    await self._finish(turn, "error")
                    yield ChatEvent("error", {"message": error.public, "partial": bool(turn.parts)})
                    return
                await self._memory.touch(context.memory_ids)
                record = await self._finish(turn, "ok")
                yield ChatEvent("done", self._done_event(conversation.id, record, turn))
                await self._bus.publish(
                    "chat.completed",
                    {
                        "conversation_id": conversation.id,
                        "message_id": record.id,
                        "channel": channel,
                    },
                )
                self._after(
                    conversation.id,
                    conversation.title == DEFAULT_TITLE and not history,
                    user,
                    record,
                    learn=learn,
                )
        finally:
            if not turn.finished:
                # Interrompu (client parti, ou coupé par la voix) : on garde ce qui a été dit.
                with contextlib.suppress(Exception):
                    await self._finish(turn, "interrupted")

    async def _reply(
        self,
        task: TaskType,
        request: LLMRequest,
        turn: _Turn,
        *,
        conversation_id: str,
        user_text: str,
        shell_folder: str | None = None,
    ) -> AsyncIterator[ChatEvent]:
        """Réponse du modèle ; s'il appelle des outils, on les traite et il reprend."""
        agent = self._agent
        limit = MAX_TOOL_ROUNDS if shell_folder is None else SHELL_TOOL_ROUNDS
        if agent is not None:
            turn.agent.known_urls |= urls_in(user_text)
            turn.agent.shell = shell_folder is not None
            request.tools = agent.specs(shell_mode=turn.agent.shell, away=turn.agent.away)
            system = request.messages[0]
            # Ce qui a vraiment été exécuté, refusé ou attend encore : Jarvis le lit, sans
            # se fier à sa mémoire de la conversation.
            states = await agent.plan_states(conversation_id)
            extra = f"\n\n{states}" if states else ""
            rules = agent.rules(shell_folder=shell_folder)
            request.messages[0] = Message("system", f"{system.content}\n\n{rules}{extra}")
        reminded = False
        for rounds in range(limit + 1):
            step = _Round()
            async for event in self._stream_round(task, request, turn, step):
                yield event
            if agent is None:
                return
            said = "".join(step.said)
            if not step.calls:
                # Un plan annoncé sans être proposé : l'utilisateur chercherait en vain quoi
                # valider. Le modèle est relancé une fois (la relance n'est pas enregistrée).
                if reminded or rounds == limit:
                    return
                if not claims_missing_plan("".join(turn.parts), turn.agent.plans):
                    return
                reminded = True
                log.warning("plan_announced_not_proposed")
                _remind_plan(request, said)
                continue
            if rounds == limit:
                # Arrêt propre : les outils de ce dernier tour ne sont pas lancés, et le modèle
                # dit où il en est, sans nouvel outil.
                log.warning("tool_rounds_exhausted", rounds=rounds)
                if said:  # un message vide, sans appel d'outil, serait refusé par l'API
                    request.messages.append(Message("assistant", said))
                request.messages.append(Message("user", ROUNDS_EXHAUSTED))
                # Les outils restent décrits (l'historique en contient, et certaines API le
                # demandent) ; ceux que le modèle appellerait encore ne sont pas lancés.
                async for event in self._stream_round(task, request, turn, _Round()):
                    yield event
                return
            request.messages.append(
                Message(
                    "assistant",
                    "".join(step.said),
                    tool_calls=tuple(step.calls),
                    reasoning="".join(step.reasoning) or None,
                    provider=turn.route.provider if turn.route else None,
                )
            )
            for call in step.calls:
                async for event in self._run_tool(
                    agent, call, request, turn, conversation_id=conversation_id, user_text=user_text
                ):
                    yield event

    async def _machine_card(self, channel: Channel) -> AsyncIterator[ChatEvent]:
        """Mode shell : la carte de la machine, relevée au premier tour (puis chaque semaine)."""
        if channel != "shell" or self._machine is None:
            return
        async with self._tracer.span("carte de la machine", kind="context") as span:
            fresh = await self._machine.ensure()
            span.set(relevee=fresh)
        if fresh:
            text = "Carte de ta machine relevée, en lecture seule : elle est dans Mémoire."
            yield ChatEvent("notice", {"text": text})

    async def _reports(
        self, conversation_id: str, after_plan: list[str] | None, ran: RanCommand | None
    ) -> list[PlanReport]:
        """Ce qui revient du terminal : les plans finis, la commande sudo lancée par toi."""
        if self._agent is None:
            return []
        reports = [await self._agent.plan_report(p, conversation_id) for p in after_plan or []]
        if ran is not None:
            reports.append(await self._agent.user_ran(ran))
        return reports

    def _after_plans(self, turn: _Turn, conversation_id: str, reports: list[PlanReport]) -> None:
        """Ce que les plans finis changent pour la suite : leurs sorties restent des données,
        et une commande qui échoue encore peut passer la main au modèle de relais."""
        _keep_marks(turn.agent, reports)
        turn.relay = self._relay_for(conversation_id, reports)

    def _relay_for(self, conversation_id: str, reports: list[PlanReport]) -> Relay | None:
        """Profil « Tout Meta » : après N échecs de la même commande (un test toujours rouge),
        ce tour passe au modèle de relais. Une réussite remet le compte de la commande à zéro."""
        failures = self._failures.setdefault(conversation_id, Counter())
        for report in reports:
            for command, failed in report.commands:
                if failed:
                    failures[command] += 1
                else:
                    failures.pop(command, None)
        policy = self._relay_policy()
        if policy is None or not failures:
            return None
        route, after = policy
        command, count = failures.most_common(1)[0]
        if count < after:
            return None
        shown = command if len(command) <= 60 else command[:57] + "…"
        return Relay(route, f"{count} échecs de « {shown} » : relais vers {route.model}")

    async def _stream_round(
        self, task: TaskType, request: LLMRequest, turn: _Turn, step: _Round
    ) -> AsyncIterator[ChatEvent]:
        """Un appel au modèle : texte diffusé au fil de l'eau, appels d'outils mis de côté."""
        before = "".join(turn.parts)
        step.checking = bool(before.strip())
        step.known = _sentences(before) if step.checking else set()
        async for event in self._router.stream_traced(
            task, request, state=turn.state, relay=turn.relay
        ):
            if isinstance(event, RouteInfo):
                turn.route = event
                relay = turn.relay
                relayed = relay is not None and relay.route.key == f"{event.provider}/{event.model}"
                yield ChatEvent(
                    "route",
                    {
                        "provider": event.provider,
                        "model": event.model,
                        "fallback_from": event.fallback_from,
                        "relay": relay.reason if relay is not None and relayed else None,
                    },
                )
            elif isinstance(event, TextDelta):
                if turn.first_token_ms is None:
                    turn.first_token_ms = (time.perf_counter() - turn.start) * 1000
                if step.checking:
                    step.held += event.text
                    text = step.unrepeated(final=False)
                else:
                    text = event.text
                if text:
                    yield self._said(turn, step, text)
            elif isinstance(event, ReasoningDelta):
                step.reasoning.append(event.text)
            elif isinstance(event, ToolCallReady):
                step.calls.append(event.call)
            elif isinstance(event, StreamEnd):
                turn.usage = Usage(
                    input_tokens=turn.usage.input_tokens + event.usage.input_tokens,
                    output_tokens=turn.usage.output_tokens + event.usage.output_tokens,
                    cached_input_tokens=turn.usage.cached_input_tokens
                    + event.usage.cached_input_tokens,
                )
        if step.checking and (text := step.unrepeated(final=True)).strip():
            yield self._said(turn, step, text)

    @staticmethod
    def _said(turn: _Turn, step: _Round, text: str) -> ChatEvent:
        if not step.said and turn.parts:
            # Texte d'un nouveau tour, après des outils : un paragraphe à part.
            text = text.lstrip()
            if not turn.parts[-1].endswith(("\n", " ")):
                text = "\n\n" + text
        step.said.append(text)
        turn.parts.append(text)
        return ChatEvent("delta", {"text": text})

    async def _run_tool(
        self,
        agent: Agent,
        call: ToolCall,
        request: LLMRequest,
        turn: _Turn,
        *,
        conversation_id: str,
        user_text: str,
    ) -> AsyncIterator[ChatEvent]:
        yield ChatEvent(
            "tool",
            {
                "id": call.id,
                "name": call.name,
                "description": agent.describe(call),
                "status": "running",
            },
        )
        async with self._tracer.span(f"outil {call.name}", kind="tool") as span:
            outcome = await agent.handle(
                call, turn.agent, conversation_id=conversation_id, request=user_text
            )
            span.set(status=outcome.event.get("status"), error=outcome.event.get("error"))
        yield ChatEvent("tool", outcome.event)
        if outcome.plan is not None:
            # Le résumé de l'agent porte les diffs et l'avis du relecteur, s'il y en a.
            summary = outcome.event.get("plan") or plan_summary(outcome.plan)
            yield ChatEvent("plan", summary)
        request.messages.append(
            Message("tool", outcome.content, tool_call_id=call.id, is_error=outcome.is_error)
        )

    def _done_event(
        self, conversation_id: str, record: MessageRecord, turn: _Turn
    ) -> dict[str, Any]:
        return {
            "conversation_id": conversation_id,
            "message_id": record.id,
            "provider": turn.route.provider if turn.route else None,
            "model": turn.route.model if turn.route else None,
            "tokens_in": turn.usage.input_tokens,
            "tokens_out": turn.usage.output_tokens,
            "cost_usd": self._router.cost(turn.state.route, turn.usage),
            "latency_ms": record.latency_ms,
            "first_token_ms": round(turn.first_token_ms, 1) if turn.first_token_ms else None,
            "trace_id": turn.trace_id,
            "plans": list(turn.agent.plans),
        }

    async def _finish(self, turn: _Turn, status: str) -> MessageRecord:
        turn.finished = True
        route = turn.route
        return await self._conversations.finish_message(
            turn.assistant.id,
            content=REDACTOR.redact("".join(turn.parts).strip()),
            status=status,
            provider=route.provider if route else None,
            model=route.model if route else None,
            tokens_in=turn.usage.input_tokens or None,
            tokens_out=turn.usage.output_tokens or None,
            cost_usd=self._router.cost(turn.state.route, turn.usage),
            latency_ms=round((time.perf_counter() - turn.start) * 1000, 1),
            trace_id=turn.trace_id or None,
            meta=_meta(turn),
        )

    # ------------------------------------------------------------------ après la réponse

    async def drain(self) -> None:
        """Attend la fin des apprentissages en cours (tests, arrêt propre)."""
        while self._background:
            await asyncio.gather(*list(self._background), return_exceptions=True)

    async def close(self) -> None:
        for task in list(self._background):
            task.cancel()
        await asyncio.gather(*list(self._background), return_exceptions=True)
