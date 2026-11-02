"""Agir à l'écran, construit pendant le hackathon Meta (O-jarvis Shell) : y trouver un élément
(SAM 3.1 encadre, Muse Spark choisit), cliquer dessus (portail de GNOME, après ta validation) et
filmer un extrait que Muse Spark explique. Ce que montre l'écran est une donnée, jamais une
instruction.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import ClassVar

import anyio
from ojarvis.core.errors import InvalidInputError, NotFoundError
from ojarvis.desktop.screen import capture
from ojarvis.tools.base import Level, Tool, ToolArgs, ToolContext, ToolOutput
from ojarvis.tools.guards import shown
from ojarvis.tools.screen import ScreenMode
from pydantic import Field

from ojarvis_shell.desktop.film import Filmer
from ojarvis_shell.desktop.images import unchanged
from ojarvis_shell.desktop.locate import Kind, Locator, Mark, ScreenMarks
from ojarvis_shell.desktop.pointer import Pointer

#: Après ta validation, le temps laissé pour que l'élément revienne à l'écran.
CLICK_WAIT_S = 15.0

CLICK_POLL_S = 1.0

LOCAL_SCREEN_ONLY = (
    "Trouver un élément envoie la capture à SAM 3.1 et à Muse Spark (Meta), et ta lecture "
    "de l'écran est réglée « sur ce PC » : rien n'est envoyé. Change-la dans Réglages › "
    "Contrôle du PC si tu veux t'en servir."
)


class FindArgs(ToolArgs):
    element: str = Field(
        min_length=2,
        max_length=200,
        description="L'élément, avec les mots affichés : « le bouton Exporter en PDF ».",
    )
    sorte: Kind = Field(
        default="bouton",
        description="Ce que c'est : bouton, icône, menu, onglet, champ, case ou lien.",
    )
    attendre_s: int = Field(
        default=0,
        ge=0,
        le=10,
        description="Secondes à attendre avant de regarder (l'utilisateur met la fenêtre devant).",
    )


class FindOnScreen(Tool[FindArgs]):
    name: ClassVar[str] = "trouver_a_l_ecran"
    description: ClassVar[str] = (
        "Trouve un élément à l'écran (bouton, icône, menu…) dans une application de bureau : "
        "SAM 3.1 encadre les éléments, Muse Spark choisit le bon. Rend un repère pour "
        "cliquer_ecran ; ne clique rien. Pour une page web, utilise plutôt le navigateur."
    )
    Args: ClassVar[type[ToolArgs]] = FindArgs
    level: ClassVar[Level] = Level.LIRE
    os_control: ClassVar[bool] = True

    def __init__(
        self,
        locator: Locator,
        mode: Callable[[], ScreenMode],
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._locator = locator
        self._mode = mode
        self._sleep = sleep

    def describe(self, args: FindArgs) -> str:
        return f"Trouver à l'écran : {args.element}"

    async def run(self, args: FindArgs, ctx: ToolContext) -> ToolOutput:
        del ctx
        if self._mode() == "local":
            raise InvalidInputError(LOCAL_SCREEN_ONLY, code="screen_local_only")
        if args.attendre_s:
            await self._sleep(args.attendre_s)
        mark = await self._locator.find(args.element, args.sorte)
        x, y = mark.center
        return ToolOutput(
            f"Trouvé : {args.element} ({mark.seen}). Repère {mark.id}, au centre ({x}, {y}) "
            f"d'une capture {mark.width}×{mark.height}. Propose maintenant le plan avec "
            f"cliquer_ecran et repere={mark.id}, sans redemander : l'utilisateur voit la "
            "capture annotée et valide dans la carte. Le repère reste valable 10 minutes.",
            {"repere": mark.id, "x": x, "y": y},
            external=True,
            source="ton écran",
        )


class ClickArgs(ToolArgs):
    repere: str = Field(
        pattern=r"^r[0-9a-f]{6}$", description="Le repère rendu par trouver_a_l_ecran."
    )


class ClickOnScreen(Tool[ClickArgs]):
    name: ClassVar[str] = "cliquer_ecran"
    description: ClassVar[str] = (
        "Clique au centre d'un élément trouvé par trouver_a_l_ecran (son repère, jamais des "
        "coordonnées). Par le portail de GNOME : la première fois, GNOME demande l'accord de "
        "l'utilisateur. Si l'écran a changé à cet endroit, rien n'est cliqué."
    )
    Args: ClassVar[type[ToolArgs]] = ClickArgs
    level: ClassVar[Level] = Level.AGIR
    os_control: ClassVar[bool] = True

    def __init__(
        self,
        marks: ScreenMarks,
        pointer: Pointer,
        grab: Callable[[], Awaitable[bytes]] = capture,
        *,
        wait_s: float = CLICK_WAIT_S,
        poll_s: float = CLICK_POLL_S,
    ) -> None:
        self._marks = marks
        self._pointer = pointer
        self._grab = grab
        self._wait_s = wait_s
        self._poll_s = poll_s

    def describe(self, args: ClickArgs) -> str:
        try:
            target = self._marks.get(args.repere).target
        except NotFoundError:
            return f"Cliquer à l'écran (repère {args.repere})"
        return f"Cliquer sur {target} (repère {args.repere})"

    async def _visible(self, mark: Mark) -> bool:
        """L'élément revient-il à l'écran ? À la validation, la fenêtre de Jarvis est devant :
        elle se réduit, et l'on attend de revoir l'élément (le 31/10, le clic était refusé)."""
        deadline = time.monotonic() + self._wait_s
        while True:
            if await anyio.to_thread.run_sync(unchanged, mark, await self._grab()):
                return True
            if time.monotonic() >= deadline:
                return False
            await asyncio.sleep(self._poll_s)

    async def run(self, args: ClickArgs, ctx: ToolContext) -> ToolOutput:
        mark = self._marks.get(args.repere)
        if not await self._visible(mark):
            raise InvalidInputError(
                f"« {mark.target} » n'est pas revenu à l'écran en {self._wait_s:.0f} s (une "
                "fenêtre est devant, ou l'écran a changé) : rien n'a été cliqué. Mets la "
                "fenêtre devant, puis retrouve l'élément.",
                code="screen_changed",
            )
        x, y = mark.center
        await self._pointer.click(x, y, (mark.width, mark.height))
        return ToolOutput(
            f"Clic fait sur « {mark.target} » en ({x}, {y}). Capture annotée : "
            f"{shown(mark.image, ctx.home)}.",
            {"repere": mark.id, "x": x, "y": y},
        )


class FilmArgs(ToolArgs):
    question: str = Field(
        min_length=2,
        max_length=500,
        description="Ce que l'utilisateur veut comprendre de ce qui va se passer à l'écran.",
    )
    duree_s: int = Field(
        default=15, ge=3, le=30, description="Durée de l'extrait, en secondes (30 au plus)."
    )


class FilmScreen(Tool[FilmArgs]):
    name: ClassVar[str] = "filmer_ecran"
    description: ClassVar[str] = (
        "Filme l'écran quelques secondes (30 au plus), seulement quand l'utilisateur le "
        "demande (« filme pendant que je reproduis le bug »), puis Muse Spark explique ce qui "
        "s'est passé. Dis-lui avant que l'enregistrement commence. La vidéo n'est gardée nulle "
        "part."
    )
    Args: ClassVar[type[ToolArgs]] = FilmArgs
    level: ClassVar[Level] = Level.LIRE
    os_control: ClassVar[bool] = True

    def __init__(self, filmer: Filmer, mode: Callable[[], ScreenMode]) -> None:
        self._filmer = filmer
        self._mode = mode

    def describe(self, args: FilmArgs) -> str:
        return f"Filmer ton écran pendant {args.duree_s} s : « {args.question} »"

    async def run(self, args: FilmArgs, ctx: ToolContext) -> ToolOutput:
        del ctx
        if self._mode() == "local":
            raise InvalidInputError(
                "Filmer envoie la vidéo à Muse Spark (Meta), et ta lecture de l'écran est "
                "réglée « sur ce PC » : rien n'est filmé. Change-la dans Réglages › Contrôle "
                "du PC si tu veux t'en servir.",
                code="screen_local_only",
            )
        text = await self._filmer.film(args.question, args.duree_s)
        return ToolOutput(
            text,
            {"duree_s": args.duree_s, "modele": "muse-spark-1.3"},
            external=True,
            source="ton écran (vidéo)",
        )
