"""Le secours visuel dans l'onglet : repérer sur une capture (SAM 3.1 et Muse Spark, ici
simulés), puis cliquer au point, en pixels de la page, si rien n'a bougé."""

from __future__ import annotations

import asyncio
import io
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from PIL import Image

from ojarvis.chrome.bridge import ChromeBridge, TabResult
from ojarvis.desktop.locate import Kind, Mark, ScreenMarks
from ojarvis.desktop.sam import Box
from ojarvis.tools.base import Level
from ojarvis.tools.tab import TabActionError
from ojarvis.tools.tab_sight import FindTabArgs, MarkArgs, TabClickMark, TabFind, TabSight


def capture(square_at: int) -> bytes:
    """Une capture de 400 × 200 pixels (le double de la page : écran HiDPI)."""
    image = Image.new("RGB", (400, 200), "white")
    image.paste((20, 20, 20), (square_at, 40, square_at + 40, 80))
    out = io.BytesIO()
    image.save(out, "JPEG", quality=95)
    return out.getvalue()


class FakeLocator:
    def __init__(self, marks: ScreenMarks) -> None:
        self.marks = marks

    async def find_in(self, image: bytes, target: str, kind: Kind) -> Mark:
        del kind
        box = Box(1, 10, 40, 49, 79)
        return self.marks.keep(target, box, image, (400, 200), "une icône LI ronde")


class Panel:
    """Le panneau simulé : rend des captures, et note les clics au point."""

    def __init__(self, bridge: ChromeBridge, captures: list[bytes]) -> None:
        self.bridge = bridge
        self.captures = captures
        self.clicks: list[dict[str, Any]] = []

    async def serve(self, count: int) -> None:
        for _ in range(count):
            action = await self.bridge.next(2.0)
            assert action is not None
            if action.kind == "capturer":
                result = TabResult(True, "Capture faite.", self.captures.pop(0), 200, 100)
            else:
                self.clicks.append({**action.args, "level": action.level})
                result = TabResult(True, "Cliqué au point repéré, sur « LI ».")
            self.bridge.resolve(action.id, result)


@pytest.fixture
def sight(tmp_path: Path) -> TabSight:
    return TabSight(ChromeBridge(), FakeLocator(ScreenMarks(tmp_path)))  # type: ignore[arg-type]


async def test_a_found_element_is_clicked_at_its_place_in_the_page(sight: TabSight) -> None:
    panel = Panel(sight.bridge, [capture(10), capture(10)])
    serving = asyncio.create_task(panel.serve(3))
    await asyncio.sleep(0)
    context: Any = SimpleNamespace()
    found = await TabFind(sight).run(FindTabArgs(element="l'icône LI à gauche"), context)
    mark_id = found.data["repere"]
    assert "une icône LI ronde" in found.text
    click = TabClickMark(sight)
    assert click.level_for(MarkArgs(repere=mark_id)) == Level.AGIR
    await click.run(MarkArgs(repere=mark_id), context)
    await serving
    # Centre (29, 59) sur la capture, page deux fois plus petite : (14, 30) en pixels CSS.
    assert panel.clicks == [{"x": 14, "y": 30, "cible": "l'icône LI à gauche", "level": 2}]


async def test_nothing_is_clicked_if_the_tab_changed(sight: TabSight) -> None:
    panel = Panel(sight.bridge, [capture(10), capture(200)])  # l'icône a bougé
    serving = asyncio.create_task(panel.serve(2))
    await asyncio.sleep(0)
    context: Any = SimpleNamespace()
    found = await TabFind(sight).run(FindTabArgs(element="l'icône LI"), context)
    with pytest.raises(TabActionError, match="a changé"):
        await TabClickMark(sight).run(MarkArgs(repere=found.data["repere"]), context)
    await serving
    assert panel.clicks == []
