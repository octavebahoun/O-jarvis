"""Cliquer dans une application en un seul geste : la fenêtre ramenée, l'élément trouvé au
moment du clic, puis le clic, le tout validé une fois (tournage du 2/11/2026)."""

from __future__ import annotations

from pathlib import Path

import pytest
from ojarvis.core.errors import InvalidInputError
from ojarvis.tools.base import Level
from ojarvis.tools.screen import ScreenMode

from ojarvis_shell.desktop.pointer import RETURN, SUPER, Key, Text
from ojarvis_shell.tools.app_click import AppClickArgs, ClickInApp
from tests.test_bring_window import FakeKeyboard, screen, seeing
from tests.test_screen_click import BUTTONS, POINTED, FakePointer, FakeSam, context, locator

PICK = {"numero": 1, "candidats": [], "vu": "en haut"}
CHECK = {"bon": True, "vu": "Bouton Exporter en PDF"}


def tool(
    tmp_path: Path, mode: ScreenMode = "en_ligne"
) -> tuple[ClickInApp, FakeKeyboard, FakePointer]:
    router, _ = seeing("OUI", "OUI")  # la vue des activités s'ouvre, puis Writer est devant
    keyboard = FakeKeyboard()
    pointer = FakePointer()
    found = locator(tmp_path, FakeSam({"button": BUTTONS}), POINTED, PICK, CHECK)
    click = ClickInApp(router, keyboard, found, pointer, lambda: mode, grab=screen)
    return click, keyboard, pointer


async def test_the_window_comes_back_then_the_element_is_found_and_clicked(
    tmp_path: Path,
) -> None:
    click, keyboard, pointer = tool(tmp_path)
    args = AppClickArgs(application="LibreOffice Writer", element="le bouton Exporter en PDF")
    out = await click.run(args, context(tmp_path))
    typed = [s for s in keyboard.steps if isinstance(s, (Key, Text))]
    assert typed == [Key(SUPER), Text("LibreOffice Writer"), Key(RETURN)]
    assert pointer.clicks == [(69, 39, (400, 200))]  # sur la capture prise après
    assert "Clic fait sur le bouton Exporter en PDF" in out.text
    assert out.data["application"] == "LibreOffice Writer"


async def test_without_an_application_nothing_is_typed(tmp_path: Path) -> None:
    click, keyboard, pointer = tool(tmp_path)
    await click.run(AppClickArgs(element="le bouton Exporter en PDF"), context(tmp_path))
    assert keyboard.steps == []
    assert len(pointer.clicks) == 1


async def test_a_screen_kept_on_this_pc_is_never_sent(tmp_path: Path) -> None:
    click, keyboard, pointer = tool(tmp_path, mode="local")
    with pytest.raises(InvalidInputError):
        await click.run(AppClickArgs(element="le bouton Exporter en PDF"), context(tmp_path))
    assert keyboard.steps == []
    assert pointer.clicks == []


def test_one_step_in_the_plan_reads_in_words(tmp_path: Path) -> None:
    click, _, _ = tool(tmp_path)
    args = AppClickArgs(application="LibreOffice Writer", element="le bouton Exporter en PDF")
    assert click.describe(args) == (
        "Dans « LibreOffice Writer », cliquer sur le bouton Exporter en PDF "
        "(repéré par SAM 3.1 au moment du clic)"
    )
    assert click.level_for(args) == Level.AGIR
    assert click.level_for(AppClickArgs(element="le bouton Supprimer")) == Level.DETRUIRE
