"""La seule porte d'exécution des outils : niveau vérifié, validation exigée, journal tenu.

Aucun outil ne s'exécute ailleurs qu'ici. Niveau 1 : directement. Niveau 2 : seulement avec
une validation (une étape d'un plan approuvé). Niveau 3 : avec une double confirmation.
Chaque action de niveau 2 ou 3 est écrite au journal immuable, avant puis après.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ojarvis.audit.journal import AuditJournal
from ojarvis.core.errors import EngineError
from ojarvis.tools.base import Level, Tool, ToolArgs, ToolContext, ToolOutput, ToolRegistry


@dataclass(frozen=True)
class Approval:
    """Ta validation, rattachée à une étape précise d'un plan et au niveau que tu as vu."""

    plan_id: str
    step: int
    level: int
    double_confirmed: bool = False


class ApprovalRequiredError(EngineError):
    status_code = 403
    code = "approval_required"


class OsControlDisabledError(EngineError):
    status_code = 403
    code = "os_control_disabled"


class ChromeUnavailableError(EngineError):
    status_code = 409
    code = "chrome_unavailable"


class PermissionGate:
    def __init__(
        self,
        registry: ToolRegistry,
        journal: AuditJournal,
        context: Callable[[], ToolContext],
        *,
        os_control: Callable[[], bool] = lambda: True,
        chrome: Callable[[], bool] = lambda: False,
    ) -> None:
        self.registry = registry
        self._journal = journal
        self._context = context
        #: Contrôle du PC activé dans Réglages ? (désactivé par défaut)
        self.os_control = os_control
        #: L'extension Chrome d'O-jarvis est-elle branchée ?
        self.chrome = chrome

    def check(self, name: str, raw_args: dict[str, Any]) -> tuple[Tool[Any], ToolArgs, Level]:
        """Outil connu, arguments valides, niveau calculé : sans rien exécuter."""
        tool = self.registry.get(name)
        if tool.os_control and not self.os_control():
            raise OsControlDisabledError(
                "Le contrôle du PC est désactivé : active-le dans Réglages → Contrôle du PC."
            )
        if tool.chrome_tab and not self.chrome():
            raise ChromeUnavailableError(
                "L'extension Chrome d'O-jarvis n'est pas branchée : ouvre Chrome (avec "
                "l'extension installée) pour que je voie et agisse dans tes onglets."
            )
        args = tool.parse(raw_args)
        return tool, args, tool.level_for(args)

    def preview(self, name: str, raw_args: dict[str, Any]) -> str | None:
        """L'aperçu d'une action (un diff), sans rien exécuter."""
        tool, args, _level = self.check(name, raw_args)
        return tool.preview(args, self._context().home)

    async def note_user_command(self, command: str, code: int) -> None:
        """Une commande lancée par l'utilisateur lui-même (sudo, depuis le terminal)."""
        await self._journal.append(
            actor="utilisateur",
            action="commande.utilisateur",
            level=int(Level.AGIR),
            summary=f"Lancée par toi : {command}",
            details={"code": code},
        )

    async def execute(
        self, name: str, raw_args: dict[str, Any], *, approval: Approval | None = None
    ) -> ToolOutput:
        tool, args, level = self.check(name, raw_args)
        summary = tool.describe(args)
        # Toute commande du mode shell va au journal, lectures comprises : elle touche ta machine.
        journaled = level >= Level.AGIR or tool.shell_only
        plan_id = approval.plan_id if approval else None
        if level >= Level.AGIR:
            if approval is None:
                raise ApprovalRequiredError(f"« {summary} » demande ta validation.")
            if level > approval.level:
                # Ex. : une adresse tirée d'une étape précédente mène à un site où tu es
                # connecté. Tu n'as pas validé l'usage de tes cookies : rien n'est fait.
                raise ApprovalRequiredError(
                    f"« {summary} » demande un niveau plus élevé que celui validé."
                )
            if level >= Level.DETRUIRE and not approval.double_confirmed:
                raise ApprovalRequiredError(f"« {summary} » demande une double confirmation.")
        if journaled:
            await self._journal.append(
                actor="jarvis",
                action=f"outil.{name}",
                level=int(level),
                summary=f"Lancé : {summary}",
                details={
                    "plan": plan_id,
                    "etape": approval.step if approval else None,
                    "arguments": raw_args,
                },
            )
        try:
            output = await tool.run(args, self._context())
        except Exception as exc:
            if journaled:
                await self._journal.append(
                    actor="jarvis",
                    action=f"outil.{name}.echec",
                    level=int(level),
                    summary=f"Échec : {summary}",
                    details={"erreur": str(exc)[:300], "plan": plan_id},
                )
            raise
        if journaled:
            await self._journal.append(
                actor="jarvis",
                action=f"outil.{name}.fait",
                level=int(level),
                summary=f"Fait : {summary}",
                details={"resultat": output.text[:300], "plan": plan_id},
            )
        return output
