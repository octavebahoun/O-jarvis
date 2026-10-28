"""La carte de la machine, lue ou relevée de nouveau à la demande (`ojarvis.shell.machine`)."""

from __future__ import annotations

from typing import ClassVar

from pydantic import Field

from ojarvis.shell.machine import SOURCE, MachineCard
from ojarvis.tools.base import Level, Tool, ToolArgs, ToolContext, ToolOutput


class MachineArgs(ToolArgs):
    rafraichir: bool = Field(
        default=False,
        description="La relever de nouveau (après une installation, un nouveau disque…).",
    )


class ReadMachineMap(Tool[MachineArgs]):
    name: ClassVar[str] = "carte_machine"
    description: ClassVar[str] = (
        "La carte de la machine de l'utilisateur : matériel, système, disques, outils "
        "installés, services en marche, projets. Relue telle qu'elle est rangée dans Mémoire, "
        "ou relevée de nouveau (rafraichir=true), toujours en lecture seule."
    )
    Args: ClassVar[type[ToolArgs]] = MachineArgs
    level: ClassVar[Level] = Level.LIRE

    def __init__(self, card: MachineCard) -> None:
        self._card = card

    def describe(self, args: MachineArgs) -> str:
        return (
            "Relever la carte de ta machine" if args.rafraichir else "Lire la carte de ta machine"
        )

    async def run(self, args: MachineArgs, ctx: ToolContext) -> ToolOutput:
        del ctx
        if args.rafraichir:
            lines = await self._card.refresh()
        else:
            await self._card.ensure()
            lines = await self._card.lines()
        text = "Carte de la machine :\n" + "\n".join(f"- {line}" for line in lines)
        return ToolOutput(text, {"lignes": lines}, external=True, source=SOURCE)
