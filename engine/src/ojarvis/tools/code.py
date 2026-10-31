"""Exécution de code Python dans le bac à sable, et liste des capacités de Jarvis."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, ClassVar

from pydantic import Field

from ojarvis.core.errors import UnavailableError
from ojarvis.tools.base import LEVEL_LABELS, Level, Tool, ToolArgs, ToolContext, ToolOutput

if TYPE_CHECKING:
    from ojarvis.tools.base import ToolRegistry


class CodeArgs(ToolArgs):
    code: str = Field(
        min_length=1,
        max_length=20_000,
        description="Programme Python 3 complet ; écrire le résultat avec print().",
    )


class RunCode(Tool[CodeArgs]):
    """Niveau 1 : le code tourne isolé (ni réseau, ni accès à tes fichiers, durée bornée)."""

    name: ClassVar[str] = "executer_code"
    description: ClassVar[str] = (
        "Exécute un programme Python 3 dans un bac à sable sans réseau ni accès à tes fichiers "
        "(calculs, conversions, analyse de texte)."
    )
    Args: ClassVar[type[ToolArgs]] = CodeArgs
    level: ClassVar[Level] = Level.LIRE

    def describe(self, args: CodeArgs) -> str:
        lines = args.code.count("\n") + 1
        return f"Exécuter un programme Python de {lines} lignes dans le bac à sable"

    async def run(self, args: CodeArgs, ctx: ToolContext) -> ToolOutput:
        if ctx.sandbox is None:
            raise UnavailableError("Bac à sable indisponible.")
        result = await ctx.sandbox.run_python(args.code)
        parts = [f"Sortie :\n{result.stdout.strip() or '(rien)'}"]
        if result.stderr.strip():
            parts.append(f"Erreurs :\n{result.stderr.strip()}")
        if result.timed_out:
            parts.append("Arrêté : temps d'exécution dépassé.")
        return ToolOutput(
            "\n".join(parts),
            {"sortie": result.stdout, "code_retour": result.exit_code, "isolement": result.backend},
            external=True,
            source="bac à sable",
        )


class NoArgs(ToolArgs):
    pass


class ListCapabilities(Tool[NoArgs]):
    name: ClassVar[str] = "lister_capacites"
    description: ClassVar[str] = (
        "Liste les outils de Jarvis, leur niveau de permission, ses skills et ses serveurs MCP."
    )
    Args: ClassVar[type[ToolArgs]] = NoArgs
    level: ClassVar[Level] = Level.LIRE

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry
        #: Sections ajoutées par les registres (skills, serveurs MCP) : titre → lignes.
        self._sections: dict[str, Callable[[], list[str]]] = {}
        self._hidden: Callable[[dict[str, Any]], bool] = lambda item: False

    def hide(self, rule: Callable[[dict[str, Any]], bool]) -> None:
        """Les outils indisponibles pour l'instant (l'extension Chrome débranchée) ne sont pas
        annoncés : Jarvis ne dit pas voir ce qu'il ne voit pas."""
        self._hidden = rule

    def add_section(self, title: str, lines: Callable[[], list[str]]) -> None:
        self._sections[title] = lines

    def describe(self, args: NoArgs) -> str:
        del args
        return "Lister les capacités de Jarvis"

    async def run(self, args: NoArgs, ctx: ToolContext) -> ToolOutput:
        del args, ctx
        lines = ["Outils :"]
        for item in self._registry.catalog():
            if self._hidden(item):
                continue
            level = Level(item["level"])
            lines.append(f"- {item['name']} : {item['description']} [{LEVEL_LABELS[level]}]")
        for title, section in self._sections.items():
            found = section()
            lines.append(f"{title} :" if found else f"{title} : aucun pour l'instant.")
            lines += found
        return ToolOutput("\n".join(lines), {"outils": self._registry.catalog()})
