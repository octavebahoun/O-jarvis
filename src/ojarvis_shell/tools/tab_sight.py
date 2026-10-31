"""Le secours visuel dans ton onglet Chrome : quand la page ne nomme pas un élément (une icône
sans nom, un dessin dans un canvas), Jarvis le repère sur une capture de l'onglet, comme à
l'écran du bureau (#48) : SAM 3.1 encadre, Muse Spark choisit et vérifie sur un zoom.

Repérer est une lecture ; cliquer au repère passe par un plan. Avant le clic, une nouvelle
capture vérifie que l'élément est toujours là.
"""

from __future__ import annotations

import asyncio
from typing import ClassVar

from pydantic import Field

from ojarvis.chrome.bridge import ChromeBridge, TabAction
from ojarvis.core.errors import NotFoundError
from ojarvis.desktop.images import unchanged
from ojarvis.desktop.locate import Kind, Locator
from ojarvis.tools.base import Level, Tool, ToolArgs, ToolContext, ToolOutput
from ojarvis.tools.tab import TabActionError, risk_level


class TabSight:
    """Ce que partagent repérer et cliquer : le pont, le repérage, et la taille de la page
    (en pixels CSS) de chaque capture, pour placer le clic."""

    def __init__(self, bridge: ChromeBridge, locator: Locator) -> None:
        self.bridge = bridge
        self.locator = locator
        self.sizes: dict[str, tuple[int, int]] = {}

    async def capture(self) -> tuple[bytes, int, int]:
        result = await self.bridge.run(TabAction("capturer", {}, int(Level.LIRE)))
        if not result.ok or not result.image:
            raise TabActionError(result.text or "Capture de l'onglet impossible.")
        return result.image, result.width, result.height


class FindTabArgs(ToolArgs):
    element: str = Field(
        min_length=2,
        max_length=200,
        description="L'élément tel qu'on le voit : « l'icône ronde LI tout à gauche ».",
    )
    sorte: Kind = Field(
        default="icône",
        description="Ce que c'est : bouton, icône, menu, onglet, champ, case ou lien.",
    )


class TabFind(Tool[FindTabArgs]):
    name: ClassVar[str] = "onglet_reperer"
    description: ClassVar[str] = (
        "Repère un élément sur une capture de l'onglet Chrome de l'utilisateur, quand il "
        "n'est pas dans la liste des éléments de la page (icône sans nom, canvas). Rend un "
        "repère pour onglet_cliquer_repere."
    )
    Args: ClassVar[type[ToolArgs]] = FindTabArgs
    level: ClassVar[Level] = Level.LIRE
    chrome_only: ClassVar[bool] = True

    def __init__(self, sight: TabSight) -> None:
        self._sight = sight

    def describe(self, args: FindTabArgs) -> str:
        return f"Repérer « {args.element} » sur une capture de ton onglet"

    async def run(self, args: FindTabArgs, ctx: ToolContext) -> ToolOutput:
        del ctx
        image, width, height = await self._sight.capture()
        mark = await self._sight.locator.find_in(image, args.element, args.sorte)
        self._sight.sizes[mark.id] = (width, height)
        return ToolOutput(
            f"Trouvé : {mark.seen or mark.target} (repère {mark.id}). Pour cliquer dessus : "
            "onglet_cliquer_repere avec ce repère, dans un plan.",
            {"repere": mark.id},
            external=True,
            source="onglet",
        )


class MarkArgs(ToolArgs):
    repere: str = Field(
        pattern=r"^r[0-9a-f]{6}$", description="Le repère rendu par onglet_reperer."
    )


class TabClickMark(Tool[MarkArgs]):
    name: ClassVar[str] = "onglet_cliquer_repere"
    description: ClassVar[str] = (
        "Clique dans l'onglet Chrome de l'utilisateur au centre d'un élément repéré par "
        "onglet_reperer (son repère, jamais des coordonnées). Si la page a changé, rien "
        "n'est cliqué."
    )
    Args: ClassVar[type[ToolArgs]] = MarkArgs
    level: ClassVar[Level] = Level.AGIR
    chrome_only: ClassVar[bool] = True

    def __init__(self, sight: TabSight) -> None:
        self._sight = sight

    def _target(self, args: MarkArgs) -> str:
        try:
            return self._sight.locator.marks.get(args.repere).target
        except NotFoundError:
            return f"le repère {args.repere}"

    def level_for(self, args: MarkArgs) -> Level:
        return risk_level(self._target(args))

    def describe(self, args: MarkArgs) -> str:
        return f"Cliquer sur {self._target(args)} dans ton onglet (repéré sur une capture)"

    async def run(self, args: MarkArgs, ctx: ToolContext) -> ToolOutput:
        del ctx
        mark = self._sight.locator.marks.get(args.repere)
        size = self._sight.sizes.get(mark.id)
        if size is None:
            raise NotFoundError("Ce repère ne vient pas de l'onglet : repère l'élément à nouveau.")
        image, _, _ = await self._sight.capture()
        if not await asyncio.to_thread(unchanged, mark, image):
            raise TabActionError(
                f"L'onglet a changé depuis le repérage de « {mark.target} » : rien n'a été "
                "cliqué. Repère l'élément à nouveau."
            )
        cx, cy = mark.center
        point = {
            "x": round(cx * size[0] / mark.width),
            "y": round(cy * size[1] / mark.height),
            "cible": mark.target,
        }
        level = int(self.level_for(args))
        result = await self._sight.bridge.run(TabAction("cliquer_point", point, level))
        if not result.ok:
            raise TabActionError(result.text)
        return ToolOutput(result.text, {"repere": mark.id}, external=True, source="onglet")


def sight_tools(sight: TabSight) -> list[Tool[FindTabArgs] | Tool[MarkArgs]]:
    return [TabFind(sight), TabClickMark(sight)]
