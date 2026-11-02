"""Cliquer dans une application de bureau en un seul geste, validé une fois.

Le 2/11/2026, au tournage : « clique sur Exporter en PDF dans LibreOffice », Writer caché
derrière le terminal. Jarvis proposait d'abord de ramener la fenêtre (une validation), puis le
clic (une deuxième, dans le terminal désormais caché) ; revenu au terminal, on recouvrait
Writer, et Jarvis attendait le bouton en prenant capture sur capture. Ici, tout tient dans une
étape : la fenêtre ramenée, l'élément trouvé sur une capture prise à ce moment-là (SAM 3.1
encadre, Muse Spark choisit et vérifie sur un zoom), puis le clic. On valide avant que rien ne
bouge ; la capture annotée reste au journal.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import ClassVar

import anyio
from ojarvis.core.errors import InvalidInputError
from ojarvis.desktop.screen import capture
from ojarvis.llm.router import ModelRouter
from ojarvis.tools.base import Level, Tool, ToolArgs, ToolContext, ToolOutput
from ojarvis.tools.guards import shown
from ojarvis.tools.screen import ScreenMode
from pydantic import Field

from ojarvis_shell.desktop.images import unchanged
from ojarvis_shell.desktop.locate import Kind, Locator, Mark
from ojarvis_shell.desktop.pointer import Keyboard, Pointer
from ojarvis_shell.tools.screen_actions import LOCAL_SCREEN_ONLY, BringWindow, WindowArgs
from ojarvis_shell.tools.tab import risk_level


class AppClickArgs(ToolArgs):
    application: str = Field(
        default="",
        max_length=80,
        description=(
            "L'application à ramener devant d'abord, telle que la recherche de GNOME la "
            "trouve (« LibreOffice Writer », « Fichiers ») ; vide si elle est déjà devant."
        ),
    )
    element: str = Field(
        min_length=2,
        max_length=200,
        description="L'élément, avec les mots affichés : « le bouton Exporter en PDF ».",
    )
    sorte: Kind = Field(
        default="bouton",
        description="Ce que c'est : bouton, icône, menu, onglet, champ, case ou lien.",
    )


class ClickInApp(Tool[AppClickArgs]):
    name: ClassVar[str] = "cliquer_dans_application"
    description: ClassVar[str] = (
        "Clique sur un élément d'une application de bureau en UN seul geste, validé une "
        "fois : ramène d'abord sa fenêtre devant (si application est donnée), puis SAM 3.1 "
        "et Muse Spark trouvent l'élément sur une capture prise à ce moment-là, le vérifient, "
        "et le clic part. À préférer à trouver_a_l_ecran puis cliquer_ecran dès que la "
        "fenêtre n'est pas devant (cachée par le terminal, par exemple) : un seul plan, sans "
        "autre validation. Pour un résultat en plusieurs clics (le bouton, puis celui de la "
        "fenêtre qui s'ouvre : « exporte en PDF » = Exporter en PDF, puis Exporter), mets "
        "chaque clic dans le MÊME plan : chacun est trouvé au moment où il se fait. Rien de "
        "trouvé, ou deux éléments possibles : rien n'est cliqué."
    )
    Args: ClassVar[type[ToolArgs]] = AppClickArgs
    level: ClassVar[Level] = Level.AGIR
    os_control: ClassVar[bool] = True

    def __init__(
        self,
        router: ModelRouter,
        keyboard: Keyboard,
        locator: Locator,
        pointer: Pointer,
        mode: Callable[[], ScreenMode],
        *,
        grab: Callable[[], Awaitable[bytes]] = capture,
    ) -> None:
        self._bring = BringWindow(router, keyboard, mode, grab)
        self._locator = locator
        self._pointer = pointer
        self._mode = mode
        self._grab = grab

    def level_for(self, args: AppClickArgs) -> Level:
        return risk_level(args.element)

    def describe(self, args: AppClickArgs) -> str:
        where = f"Dans « {args.application} », c" if args.application else "C"
        return f"{where}liquer sur {args.element} (repéré par SAM 3.1 au moment du clic)"

    async def _found(self, args: AppClickArgs) -> Mark:
        """L'élément sur une capture fraîche ; si l'écran bouge encore, une seconde fois."""
        mark = await self._locator.find(args.element, args.sorte)
        if await anyio.to_thread.run_sync(unchanged, mark, await self._grab()):
            return mark
        return await self._locator.find(args.element, args.sorte)

    async def run(self, args: AppClickArgs, ctx: ToolContext) -> ToolOutput:
        if self._mode() == "local":
            raise InvalidInputError(LOCAL_SCREEN_ONLY, code="screen_local_only")
        brought = ""
        if args.application:
            out = await self._bring.run(WindowArgs(application=args.application), ctx)
            brought = f"{out.text} "
        mark = await self._found(args)
        x, y = mark.center
        await self._pointer.click(x, y, (mark.width, mark.height))
        return ToolOutput(
            f"{brought}Clic fait sur {args.element} ({mark.seen}) en ({x}, {y}). Capture "
            f"annotée : {shown(mark.image, ctx.home)}.",
            {"application": args.application, "repere": mark.id, "x": x, "y": y},
        )
