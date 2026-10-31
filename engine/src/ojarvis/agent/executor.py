"""Exécution d'un plan validé : étape par étape, chacune par la porte des permissions.

Rien ne démarre sans ta décision. Un plan de niveau 3 demande une double confirmation.
Si une étape échoue, le plan s'arrête ; « Arrêter » laisse finir l'étape en cours et ne
lance pas les suivantes. À la fin, le modèle écrit une synthèse à partir des
résultats (marqués comme données, jamais comme instructions) ; elle rejoint la conversation.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from ojarvis.agent.plans import PLACEHOLDER, PlanRecord, PlanStatus, PlanStore
from ojarvis.audit.journal import AuditJournal
from ojarvis.context.engine import PERSONA
from ojarvis.conversations.store import ConversationStore
from ojarvis.core.errors import ConflictError, EngineError
from ojarvis.core.logs import get_logger
from ojarvis.events.bus import EventBus
from ojarvis.llm.errors import LLMError
from ojarvis.llm.router import ModelRouter
from ojarvis.llm.types import LLMRequest, Message
from ojarvis.permissions.gate import Approval, PermissionGate
from ojarvis.tools.guards import DATA_RULE, as_data

log = get_logger(__name__)

SYNTHESIS_RULES = (
    "Tu viens d'exécuter un plan validé par l'utilisateur : les résultats ci-dessous sont ceux "
    "de tes propres outils, et ils sont réels. Résume, dans la langue de sa demande, en deux à "
    "quatre phrases, ce qui a été fait et ce qui a été trouvé ; dis clairement si une étape a "
    "échoué. Une étape « faite » a vraiment eu lieu : ne la mets jamais en doute. Ne salue pas "
    "et ne pose pas de question, sauf pour proposer la suite quand une étape a échoué."
)


class DoubleConfirmationRequiredError(EngineError):
    status_code = 409
    code = "double_confirmation_required"


def fill_placeholders(value: Any, results: dict[int, str]) -> Any:
    """Remplace {{etape N}} par le résultat (texte) de l'étape N, dans tous les arguments."""
    if isinstance(value, str):
        return PLACEHOLDER.sub(lambda m: results.get(int(m.group(1)), "")[:2000], value)
    if isinstance(value, list):
        return [fill_placeholders(v, results) for v in value]
    if isinstance(value, dict):
        return {k: fill_placeholders(v, results) for k, v in value.items()}
    return value


class PlanExecutor:
    def __init__(
        self,
        *,
        plans: PlanStore,
        gate: PermissionGate,
        journal: AuditJournal,
        router: ModelRouter,
        conversations: ConversationStore,
        bus: EventBus,
    ) -> None:
        self._plans = plans
        self._gate = gate
        self._journal = journal
        self._router = router
        self._conversations = conversations
        self._bus = bus
        self._running: dict[str, asyncio.Task[None]] = {}
        #: Plans que tu as arrêtés : leur prochaine étape ne part pas.
        self._stopping: set[str] = set()

    async def _publish(self, plan_id: str, **extra: Any) -> None:
        await self._bus.publish("plan.updated", {"id": plan_id, **extra}, persist=False)

    async def approve(
        self, plan_id: str, *, double_confirm: bool = False, channel: str = "app"
    ) -> PlanRecord:
        plan = await self._plans.get(plan_id)
        if plan.status != "proposed":
            raise ConflictError("Ce plan n'attend plus de décision.", code="plan_decided")
        if plan.max_level >= 3 and not double_confirm:
            raise DoubleConfirmationRequiredError(
                "Ce plan contient une action destructrice : confirme une seconde fois."
            )
        await self._plans.set_status(plan_id, "approved", decided=True)
        await self._journal.append(
            actor="utilisateur",
            action="plan.approuve",
            level=plan.max_level,
            summary=f"Plan validé : {plan.title}",
            details={
                "plan": plan_id,
                "etapes": [s.description for s in plan.steps],
                "double_confirmation": double_confirm,
                "propose_apres_contenu_externe": plan.tainted,
                "canal": channel,
            },
        )
        await self._publish(plan_id, status="approved")
        self._launch(plan_id, double_confirmed=double_confirm)
        return await self._plans.get(plan_id)

    async def start(self, plan_id: str) -> PlanRecord:
        """Plan qui ne fait que lire (niveau 1), sans contenu externe lu avant : comme un
        outil de lecture, il part sans attendre (« validation selon le niveau »)."""
        plan = await self._plans.get(plan_id)
        if plan.status != "proposed" or plan.max_level > 1 or plan.tainted:
            raise ConflictError("Ce plan attend ta décision.", code="plan_needs_decision")
        await self._plans.set_status(plan_id, "approved", decided=True)
        await self._publish(plan_id, status="approved")
        self._launch(plan_id, double_confirmed=False)
        return await self._plans.get(plan_id)

    def _launch(self, plan_id: str, *, double_confirmed: bool) -> None:
        task = asyncio.create_task(self.run(plan_id, double_confirmed=double_confirmed))
        self._running[plan_id] = task

        def finished(_task: asyncio.Task[None]) -> None:
            self._running.pop(plan_id, None)
            self._stopping.discard(plan_id)

        task.add_done_callback(finished)

    async def stop(self, plan_id: str, *, channel: str = "app") -> PlanRecord:
        """« Arrêter » : l'étape en cours se termine, les suivantes ne sont pas lancées."""
        plan = await self._plans.get(plan_id)
        if plan_id not in self._running or plan.status not in ("approved", "running"):
            raise ConflictError("Ce plan ne tourne plus.", code="plan_not_running")
        self._stopping.add(plan_id)
        await self._journal.append(
            actor="utilisateur",
            action="plan.arrete",
            level=plan.max_level,
            summary=f"Plan arrêté : {plan.title}",
            details={"plan": plan_id, "canal": channel},
        )
        await self._publish(plan_id, stopping=True)
        return await self._plans.get(plan_id)

    async def refuse(self, plan_id: str, *, channel: str = "app") -> PlanRecord:
        plan = await self._plans.get(plan_id)
        if plan.status != "proposed":
            raise ConflictError("Ce plan n'attend plus de décision.", code="plan_decided")
        await self._plans.set_status(plan_id, "refused", decided=True)
        await self._journal.append(
            actor="utilisateur",
            action="plan.refuse",
            level=plan.max_level,
            summary=f"Plan refusé : {plan.title}",
            details={"plan": plan_id, "canal": channel},
        )
        await self._publish(plan_id, status="refused")
        return await self._plans.get(plan_id)

    async def run(self, plan_id: str, *, double_confirmed: bool = False) -> None:
        plan = await self._plans.get(plan_id)
        await self._plans.set_status(plan_id, "running")
        await self._publish(plan_id, status="running")
        results: dict[int, str] = {}
        sections: list[str] = []
        failed = False
        stopped = False
        for step in plan.steps:
            if not (failed or stopped) and plan_id in self._stopping:
                stopped = True
                sections.append(f"Arrêté par l'utilisateur avant l'étape {step.position}.")
            if failed or stopped:
                await self._plans.set_step(plan_id, step.position, status="skipped")
                await self._publish(plan_id, step=step.position, step_status="skipped")
                continue
            await self._plans.set_step(plan_id, step.position, status="running")
            await self._publish(plan_id, step=step.position, step_status="running")
            arguments = fill_placeholders(step.arguments, results)
            approval = Approval(
                plan_id, step.position, step.level, double_confirmed=double_confirmed
            )
            try:
                output = await self._gate.execute(step.tool, arguments, approval=approval)
            except Exception as exc:
                failed = True
                message = exc.message if isinstance(exc, EngineError) else type(exc).__name__
                await self._plans.set_step(plan_id, step.position, status="failed", error=message)
                await self._publish(plan_id, step=step.position, step_status="failed")
                sections.append(f"Étape {step.position}, échec ({step.description}) : {message}")
                continue
            results[step.position] = output.text
            await self._plans.set_step(plan_id, step.position, status="done", result=output.text)
            await self._publish(plan_id, step=step.position, step_status="done")
            body = as_data(output.text, origin=output.source) if output.external else output.text
            sections.append(f"Étape {step.position}, faite ({step.description}) :\n{body}")
        status: PlanStatus = "stopped" if stopped else "failed" if failed else "done"
        if await self._in_shell(plan):
            # En mode shell, Jarvis lit lui-même les résultats au tour suivant (il corrige,
            # relance, vérifie) : un compte rendu par le modèle serait un appel de trop.
            done = len(results)
            synthesis = f"Plan « {plan.title} » : {done} étape(s) faite(s) sur {len(plan.steps)}."
            if stopped:
                synthesis += " Arrêté par toi."
        else:
            synthesis = await self._synthesize(plan, sections)
        # Le compte rendu entre dans la conversation avant que le plan soit dit fini : une
        # question posée juste après le voit déjà.
        if plan.conversation_id:
            try:
                await self._conversations.add_message(
                    plan.conversation_id, "assistant", synthesis, meta={"plan": plan_id}
                )
            except Exception:
                log.exception("plan_synthesis_not_saved", plan=plan_id)
        await self._plans.set_result(plan_id, status, synthesis)
        await self._bus.publish(
            "plan.finished",
            {"id": plan_id, "status": status, "conversation_id": plan.conversation_id},
        )

    async def _in_shell(self, plan: PlanRecord) -> bool:
        if not plan.conversation_id:
            return False
        try:
            conversation = await self._conversations.get(plan.conversation_id)
        except EngineError:
            return False
        # Le panneau de Chrome aussi : il relance Jarvis avec l'onglet, qui lit les résultats.
        return conversation.channel in ("shell", "chrome")

    async def _synthesize(self, plan: PlanRecord, sections: list[str]) -> str:
        request = LLMRequest(
            messages=[
                Message("system", f"{PERSONA}\n\n{DATA_RULE}\n\n{SYNTHESIS_RULES}"),
                Message(
                    "user",
                    "Compte rendu à écrire pour l'utilisateur.\n"
                    f"Sa demande : {plan.request}\nPlan exécuté : {plan.title}\n\n"
                    "Résultats de tes outils :\n\n" + "\n\n".join(sections),
                ),
            ],
            max_tokens=700,
        )
        try:
            result = await self._router.complete("chat", request)
        except LLMError:
            log.warning("plan_synthesis_failed", plan=plan.id)
            done = sum(1 for s in sections if "échec" not in s)
            total = len(sections)
            return f"Plan « {plan.title} » terminé : {done} étape(s) réussie(s) sur {total}."
        return result.text.strip() or json.dumps(sections, ensure_ascii=False)[:2000]

    async def recover(self) -> int:
        """Plans coupés par un arrêt du moteur : marqués en échec, jamais relancés seuls
        (une action ne se rejoue pas sans toi)."""
        stuck = await self._plans.list(("approved", "running"))
        for plan in stuck:
            for step in plan.steps:
                if step.status == "running":
                    await self._plans.set_step(
                        plan.id, step.position, status="failed", error="Interrompue par un arrêt."
                    )
                elif step.status == "pending":
                    await self._plans.set_step(plan.id, step.position, status="skipped")
            await self._plans.set_result(
                plan.id, "failed", "Plan interrompu par l'arrêt de Jarvis : rien n'a été relancé."
            )
            await self._journal.append(
                actor="jarvis",
                action="plan.interrompu",
                level=plan.max_level,
                summary=f"Plan interrompu par un arrêt : {plan.title}",
                details={"plan": plan.id},
            )
        return len(stuck)

    async def close(self) -> None:
        for task in list(self._running.values()):
            task.cancel()
        await asyncio.gather(*self._running.values(), return_exceptions=True)
