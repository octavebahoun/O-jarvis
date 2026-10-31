"""Ce que Jarvis fait d'un appel d'outil pendant une conversation.

- Outil de niveau 1 (lire) : exécuté tout de suite, par la porte des permissions ; son
  résultat revient au modèle encadré comme une donnée s'il vient de l'extérieur.
- `proposer_un_plan` : le plan est vérifié, enregistré, et attend ta décision.
- Appel direct d'une action (niveau 2 ou 3) : jamais exécuté ; il devient un plan
  d'une étape, à valider comme les autres.
"""

from __future__ import annotations

from dataclasses import replace

import anyio.to_thread
from pydantic import ValidationError

from ojarvis.agent.executor import PlanExecutor
from ojarvis.agent.measures import MEASURE_AFTER, MEASURE_BEFORE
from ojarvis.agent.plans import (
    FINISHED,
    MAX_STEPS,
    PLACEHOLDER,
    PLAN_TOOL,
    PlanIn,
    PlanStore,
    StepIn,
    plan_tool_spec,
)
from ojarvis.agent.reports import (
    SHELL_TOOL,
    PlanReport,
    RanCommand,
    command_failed,
    from_web,
    plan_summary,
    urls_in,
)
from ojarvis.agent.review import CodeReviewer
from ojarvis.agent.rules import (
    AGENT_RULES,
    CHROME_ONLY,
    NO_TERMINAL,
    OS_CONTROL_OFF,
    OS_CONTROL_ON,
    PLAN_PENDING,
    PLAN_STARTED,
    PLAN_STATES,
    SHELL_ONLY,
    SHELL_RULES,
)
from ojarvis.agent.turn import AgentTurn, CallOutcome, ChromeOnlyError, ShellOnlyError
from ojarvis.core.errors import ConflictError, EngineError, InvalidInputError, NotFoundError
from ojarvis.core.logs import get_logger
from ojarvis.events.bus import EventBus
from ojarvis.llm.types import ToolCall, ToolSpec
from ojarvis.permissions.gate import PermissionGate
from ojarvis.security.redaction import REDACTOR
from ojarvis.shell.signatures import talks_to_network
from ojarvis.tools.base import LEVEL_LABELS, Level
from ojarvis.tools.guards import as_data

log = get_logger(__name__)

#: Fin de la sortie d'une commande montrée dans le terminal (le modèle lit tout).
OUTPUT_PREVIEW_CHARS = 2000
#: Tours d'outils au plus dans une réponse (ensuite, le modèle doit conclure). Une PR
#: GitHub en demande souvent 6 ou 7 : lister les outils, lire le dépôt, puis le plan.
MAX_TOOL_ROUNDS = 8


class Agent:
    def __init__(
        self,
        *,
        gate: PermissionGate,
        plans: PlanStore,
        bus: EventBus,
        executor: PlanExecutor | None = None,
        reviewer: CodeReviewer | None = None,
    ) -> None:
        self._gate = gate
        self._plans = plans
        self._bus = bus
        self._executor = executor
        #: Mode shell : relit chaque modification de code avant ta validation.
        self._reviewer = reviewer

    async def plan_states(self, conversation_id: str) -> str:
        """L'état réel des plans de la conversation, du plus ancien au plus récent."""
        plans = await self._plans.list(limit=8, conversation_id=conversation_id)
        if not plans:
            return ""
        lines = [
            f"- « {' '.join(p.title.split())[:80]} » : {PLAN_STATES.get(p.status, p.status)}"
            for p in reversed(plans)
        ]
        return "État réel des plans de cette conversation (du plus ancien au plus récent) :\n" + (
            "\n".join(lines)
        )

    async def plan_report(self, plan_id: str, conversation_id: str) -> PlanReport:
        """Les résultats d'un plan fini de cette conversation, marqués comme données : en mode
        shell, Jarvis les lit pour continuer (corriger, relancer, vérifier)."""
        plan = await self._plans.get(plan_id)
        if plan.conversation_id != conversation_id:
            raise NotFoundError("Ce plan n'appartient pas à cette conversation.")
        if plan.status not in FINISHED:
            raise ConflictError("Ce plan n'est pas encore terminé.")
        if plan.status == "refused":
            refused = f"L'utilisateur a refusé le plan « {plan.title} » : rien n'a été lancé."
            return PlanReport(refused, plan.request, tainted=plan.tainted)
        ran = [step for step in plan.steps if step.status == "done"]
        commands = [str(s.arguments.get("commande", "")) for s in ran if s.tool == SHELL_TOOL]
        report = PlanReport(
            "",
            plan.request,
            # Des sorties de commandes ou de fichiers : des données, jamais des consignes.
            tainted=plan.tainted or bool(ran),
            web=any(talks_to_network(command) for command in commands if command),
            urls=frozenset(urls_in([step.result or "" for step in ran])),
            commands=tuple(
                (" ".join(str(step.arguments.get("commande", "")).split()), command_failed(step))
                for step in plan.steps
                if step.tool == SHELL_TOOL and step.status in ("done", "failed")
            ),
        )
        sections = []
        measured = any(step.why == MEASURE_BEFORE for step in plan.steps)
        for step in plan.steps:
            if step.why in (MEASURE_BEFORE, MEASURE_AFTER) and step.status == "done":
                label = "Mesure avant" if step.why == MEASURE_BEFORE else "Mesure après"
                body = as_data(step.result or "", origin="commande")
                sections.append(f"{label} ({step.description}) :\n{body}")
            elif step.status == "done":
                body = as_data(step.result or "", origin="commande")
                sections.append(f"Étape {step.position}, faite ({step.description}) :\n{body}")
            elif step.status == "failed":
                sections.append(f"Étape {step.position}, échec ({step.description}) : {step.error}")
            else:
                sections.append(f"Étape {step.position}, non lancée ({step.description}).")
        state = PLAN_STATES.get(plan.status, plan.status)
        text = (
            f"Le plan validé « {plan.title} » est {state}. Résultats :\n\n"
            + "\n\n".join(sections)
            + "\n\nContinue la tâche à partir de ces résultats, ou conclus si elle est finie."
        )
        if measured:
            text += (
                " Compare la mesure d'avant et celle d'après : si l'après confirme le résultat, "
                "dis « vérifié » avec les deux chiffres ; sinon dis « non vérifié » et ce qui "
                "manque, jamais « fait »."
            )
        elif any(step.level >= Level.AGIR and step.tool == SHELL_TOOL for step in ran):
            text += (
                " Ce plan n'avait pas de mesure : ne dis pas « fait ». Vérifie par une lecture, "
                "ou dis « non vérifié »."
            )
        return replace(report, text=text)

    async def user_ran(self, ran: RanCommand) -> PlanReport:
        """Une commande lancée par l'utilisateur lui-même : au journal, puis rendue au modèle
        comme une donnée, masquée, pour qu'il continue."""
        await self._gate.note_user_command(ran.command, ran.code)
        output = REDACTOR.redact(ran.output)
        body = as_data(
            f"$ {ran.command}\ncode de sortie {ran.code}\n--- sortie ---\n{output}",
            origin="commande lancée par l’utilisateur",
        )
        text = (
            f"L'utilisateur a lancé lui-même la commande proposée (code de sortie {ran.code}). "
            f"Résultat :\n\n{body}\n\nContinue la tâche à partir de ce résultat, ou conclus si "
            "elle est finie."
        )
        return PlanReport(
            text,
            "",
            tainted=True,
            web=talks_to_network(ran.command),
            urls=frozenset(urls_in(output)),
            commands=((" ".join(ran.command.split()), ran.code != 0),),
        )

    def rules(self, *, shell_folder: str | None = None) -> str:
        """Consignes du tour, selon ce que Jarvis a le droit de faire en ce moment."""
        os_control = OS_CONTROL_ON if self._gate.os_control() else OS_CONTROL_OFF
        terminal = NO_TERMINAL if shell_folder is None else SHELL_RULES.format(folder=shell_folder)
        return f"{AGENT_RULES}\n\n{terminal}\n\n{os_control}"

    def specs(self, *, shell_mode: bool = False, chrome_mode: bool = False) -> list[ToolSpec]:
        specs = self._gate.registry.specs(
            max_level=Level.DETRUIRE,
            os_control=self._gate.os_control(),
            shell_mode=shell_mode,
            chrome_mode=chrome_mode,
        )
        return [*specs, plan_tool_spec()]

    async def _previews(self, plan_in: PlanIn) -> dict[int, str]:
        previews: dict[int, str] = {}
        touched: set[str] = set()
        after_command = False
        for position, step in enumerate(plan_in.etapes, 1):
            target = str(step.arguments.get("chemin", ""))
            if step.outil == SHELL_TOOL:
                # Une mesure ne change rien : les retouches qui la suivent gardent leur diff.
                after_command = after_command or step.pourquoi != MEASURE_BEFORE
                continue
            if (
                PLACEHOLDER.search(str(step.arguments))
                or (target and target in touched)
                or after_command
            ):
                # Cible connue seulement à l'exécution, ou fichier qu'une étape précédente a
                # pu changer ou créer : son aperçu ne serait pas juste, le résultat le montrera.
                continue
            preview = await anyio.to_thread.run_sync(self._gate.preview, step.outil, step.arguments)
            if preview is not None:
                previews[position] = preview
                touched.add(target)
        return previews

    def _measured(self, plan_in: PlanIn, turn: AgentTurn) -> PlanIn:
        """La mesure d'un plan (#15) : une lecture prise avant les étapes, puis après. Sans
        elle, Jarvis ne peut pas montrer la preuve de l'effet."""
        measure = plan_in.mesure
        if measure is None:
            return self._final_read_as_measure(plan_in, turn)
        self._require_shell(SHELL_TOOL, turn)
        folders = [
            str(s.arguments["dossier"]) for s in plan_in.etapes if s.arguments.get("dossier")
        ]
        folder = measure.dossier or (folders[0] if folders else "~")
        arguments = {"commande": measure.commande, "dossier": folder}
        _tool, _args, level = self._gate.check(SHELL_TOOL, arguments)
        if level != Level.LIRE:
            raise InvalidInputError(
                "La mesure d'un plan est une simple lecture (df, ls, systemctl status…)."
            )
        if len(plan_in.etapes) + 2 > MAX_STEPS:
            raise InvalidInputError(
                f"Avec la mesure avant et après, le plan dépasserait {MAX_STEPS} étapes."
            )
        before = StepIn(outil=SHELL_TOOL, arguments=arguments, pourquoi=MEASURE_BEFORE)
        after = StepIn(outil=SHELL_TOOL, arguments=arguments, pourquoi=MEASURE_AFTER)
        return PlanIn(titre=plan_in.titre, etapes=[before, *plan_in.etapes, after])

    def _final_read_as_measure(self, plan_in: PlanIn, turn: AgentTurn) -> PlanIn:
        """Un plan d'actions qui finit par une lecture (« touch a.txt » puis « ls -l a.txt ») :
        cette vérification devient sa mesure, prise aussi avant les actions (#15). Muse Spark
        écrit ainsi sa preuve plus souvent qu'il ne remplit la mesure (essai du 28/10/2026)."""
        last = plan_in.etapes[-1]
        if (
            not turn.shell
            or len(plan_in.etapes) < 2
            or len(plan_in.etapes) + 1 > MAX_STEPS
            or last.outil != SHELL_TOOL
            or PLACEHOLDER.search(str(last.arguments))
        ):
            return plan_in
        try:
            levels = [self._gate.check(s.outil, s.arguments)[2] for s in plan_in.etapes]
        except EngineError:
            return plan_in  # la proposition dira l'erreur
        if levels[-1] != Level.LIRE or max(levels[:-1]) < Level.AGIR:
            return plan_in
        before = StepIn(outil=SHELL_TOOL, arguments=last.arguments, pourquoi=MEASURE_BEFORE)
        after = StepIn(outil=SHELL_TOOL, arguments=last.arguments, pourquoi=MEASURE_AFTER)
        return PlanIn(titre=plan_in.titre, etapes=[before, *plan_in.etapes[:-1], after])

    def _require_shell(self, name: str, turn: AgentTurn) -> None:
        """Hors du mode shell, une commande n'est ni lancée ni proposée, même si le modèle
        appelle l'outil sans l'avoir vu, ou le glisse dans un plan."""
        registry = self._gate.registry
        if not turn.shell and name in registry and registry.get(name).shell_only:
            raise ShellOnlyError(SHELL_ONLY.format(tool=name))
        # De même, ton onglet n'est manipulé que depuis le panneau de Chrome qui l'a lu.
        if not turn.chrome and name in registry and registry.get(name).chrome_only:
            raise ChromeOnlyError(CHROME_ONLY.format(tool=name))

    def describe(self, call: ToolCall) -> str:
        """Ce que fait l'appel, en clair (pour l'interface), sans rien exécuter."""
        if call.name == PLAN_TOOL:
            return "Préparer un plan"
        try:
            tool, args, _ = self._gate.check(call.name, call.arguments)
        except EngineError:
            return call.name
        return tool.describe(args)

    async def handle(
        self,
        call: ToolCall,
        turn: AgentTurn,
        *,
        conversation_id: str | None,
        request: str,
    ) -> CallOutcome:
        if call.name == PLAN_TOOL:
            try:
                plan_in = PlanIn.model_validate(call.arguments)
            except ValidationError as exc:
                first = exc.errors()[0]
                where = ".".join(str(p) for p in first.get("loc", ()))
                return self._failed(call, f"Plan invalide ({where} : {first.get('msg')}).")
            return await self._propose(call, plan_in, turn, conversation_id, request)
        try:
            self._require_shell(call.name, turn)
            tool, args, level = self._gate.check(call.name, call.arguments)
        except EngineError as exc:
            return self._failed(call, exc.message)
        if level >= Level.AGIR:
            # Une action n'est jamais exécutée sur un simple appel : elle devient un plan.
            plan_in = PlanIn(
                titre=tool.describe(args)[:120],
                etapes=[
                    StepIn(
                        outil=call.name,
                        arguments=call.arguments,
                        pourquoi="Action demandée pendant la conversation.",
                    )
                ],
            )
            return await self._propose(call, plan_in, turn, conversation_id, request)
        url = call.arguments.get("url")
        if turn.tainted and isinstance(url, str) and not turn.knows(url):
            # Après une lecture externe, une adresse fabriquée (absente de ta demande et de ce
            # qui a été lu) pourrait servir à faire fuir des données : tu valides d'abord.
            plan_in = PlanIn(
                titre=f"Ouvrir {url}"[:120],
                etapes=[
                    StepIn(
                        outil=call.name,
                        arguments=call.arguments,
                        pourquoi=(
                            "Adresse absente de ta demande et des pages lues : elle peut "
                            "venir d'un contenu piégé."
                        ),
                    )
                ],
            )
            return await self._propose(call, plan_in, turn, conversation_id, request)
        return await self._read(
            call,
            tool.describe(args),
            int(level),
            turn,
            conversation_id=conversation_id,
            request=request,
        )

    async def _read(
        self,
        call: ToolCall,
        description: str,
        level: int,
        turn: AgentTurn,
        *,
        conversation_id: str | None,
        request: str,
    ) -> CallOutcome:
        """Outil de niveau 1 : exécuté tout de suite, par la porte des permissions."""
        try:
            output = await self._gate.execute(call.name, call.arguments)
        except EngineError as exc:
            return self._failed(call, exc.message, description)
        except Exception as exc:
            log.exception("tool_failed", tool=call.name)
            return self._failed(call, f"Erreur inattendue ({type(exc).__name__}).", description)
        event = {
            "id": call.id,
            "name": call.name,
            "description": description,
            "level": level,
            "status": "done",
            "source": output.source,
        }
        if self._gate.registry.get(call.name).shell_only:
            # Le terminal montre la fin de la sortie ; le modèle, lui, la lit en entier.
            event["output"] = REDACTOR.redact(output.text[-OUTPUT_PREVIEW_CHARS:])
        if isinstance(output.data, dict) and output.data.get("sudo"):
            event["sudo"] = output.data["sudo"]  # le terminal la propose : tu la lances
        turn.tools.append(event)
        turn.known_urls |= urls_in(output.text) | urls_in(output.data)
        if output.external:
            turn.tainted = True
            command = call.arguments.get("commande")
            turn.web = turn.web or from_web(
                output.source, command if isinstance(command, str) else None
            )
        content = as_data(output.text, origin=output.source) if output.external else output.text
        if output.follow_up is None:
            return CallOutcome(content, event)
        # L'outil propose sa suite (ex. activer un skill dont les tests ont réussi) : un plan,
        # au niveau de cette suite, qui attend ta décision.
        follow = output.follow_up
        plan_in = PlanIn(
            titre=follow.why[:120],
            etapes=[StepIn(outil=follow.tool, arguments=follow.arguments, pourquoi=follow.why)],
        )
        proposed = await self._propose(call, plan_in, turn, conversation_id, request)
        return CallOutcome(f"{content}\n\n{proposed.content}", event, plan=proposed.plan)

    async def _propose(
        self,
        call: ToolCall,
        plan_in: PlanIn,
        turn: AgentTurn,
        conversation_id: str | None,
        request: str,
    ) -> CallOutcome:
        try:
            plan_in = self._measured(plan_in, turn)
            for step in plan_in.etapes:
                self._require_shell(step.outil, turn)
            # Le diff de chaque modification, montré avant ta validation. Une retouche qui ne
            # s'appliquerait pas (passage introuvable) revient au modèle, pas à toi.
            previews = await self._previews(plan_in)
            plan = await self._plans.create(
                plan_in, conversation_id=conversation_id, request=request, tainted=turn.tainted
            )
        except EngineError as exc:
            return self._failed(call, f"Plan refusé : {exc.message} Corrige-le.")
        turn.plans.append(plan.id)
        if self._executor is not None and plan.max_level == 1 and not plan.tainted:
            plan = await self._executor.start(plan.id)
            event = {
                "id": call.id,
                "name": PLAN_TOOL,
                "description": f"Plan lancé : {plan.title}",
                "level": plan.max_level,
                "status": "proposed",
                "plan": plan_summary(plan),
            }
            turn.tools.append({k: v for k, v in event.items() if k != "plan"})
            return CallOutcome(PLAN_STARTED, event, plan=plan)
        review = None
        if previews and turn.shell and self._reviewer is not None:
            review = await self._reviewer.review(request, list(previews.values()))
        summary = plan_summary(plan, previews, review, web=turn.web)
        await self._bus.publish(
            "plan.proposed",
            {"id": plan.id, "title": plan.title, "max_level": plan.max_level},
        )
        steps = "\n".join(
            f"{s.position}. {s.description} (niveau {s.level} : {LEVEL_LABELS[Level(s.level)]})"
            for s in plan.steps
        )
        if review is not None:
            # L'auteur lit l'avis : s'il signale un problème, il peut proposer un plan corrigé.
            steps += f"\n\n{review.for_author()}"
        event = {
            "id": call.id,
            "name": PLAN_TOOL,
            "description": f"Plan proposé : {plan.title}",
            "level": plan.max_level,
            "status": "proposed",
            "plan": summary,
        }
        turn.tools.append({k: v for k, v in event.items() if k != "plan"})
        return CallOutcome(f"{PLAN_PENDING}\n\nÉtapes :\n{steps}", event, plan=plan)

    @staticmethod
    def _failed(call: ToolCall, message: str, description: str | None = None) -> CallOutcome:
        event = {
            "id": call.id,
            "name": call.name,
            "description": description or call.name,
            "status": "failed",
            "error": message,
        }
        return CallOutcome(f"Échec : {message}", event, is_error=True)
