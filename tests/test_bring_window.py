"""Ramener une fenêtre devant (au clavier, par la vue des activités de GNOME) et voir le
pointeur glisser jusqu'à sa cible."""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Any

import pytest
from ojarvis.core.errors import InvalidInputError, UnavailableError
from ojarvis.llm.catalog import Catalog
from ojarvis.llm.providers.fake import FakeProvider, FakeReply
from ojarvis.llm.router import ModelRouter
from ojarvis.llm.types import LLMRequest

import ojarvis_shell.llm.meta_profile  # noqa: F401 - « Tout Meta »
from ojarvis_shell.desktop.pointer import (
    ESCAPE,
    GLIDE_STEPS,
    RETURN,
    SUPER,
    Check,
    Key,
    KeyStep,
    Pause,
    PortalPointer,
    Stream,
    Text,
    keysym,
)
from ojarvis_shell.tools.screen_actions import BringWindow, WindowArgs
from tests.test_screen_click import context, window


class FakeRemote:
    def __init__(self) -> None:
        self.stream = Stream(7, 400, 200)
        self.moves: list[tuple[float, float]] = []
        self.buttons: list[bool] = []
        self.taps: list[int] = []

    async def move(self, sx: float, sy: float) -> None:
        self.moves.append((sx, sy))

    async def button(self, *, pressed: bool) -> None:
        self.buttons.append(pressed)

    async def tap(self, key: int) -> None:
        self.taps.append(key)


def portal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[PortalPointer, FakeRemote]:
    pointer = PortalPointer(tmp_path / "jeton")
    remote = FakeRemote()

    @contextlib.asynccontextmanager
    async def opened(what: str) -> AsyncIterator[FakeRemote]:
        del what
        yield remote

    monkeypatch.setattr(pointer, "_remote", opened)
    monkeypatch.setattr("ojarvis_shell.desktop.pointer.GLIDE_S", 0.0)
    monkeypatch.setattr("ojarvis_shell.desktop.pointer.TYPE_PAUSE_S", 0.0)
    return pointer, remote


def test_each_character_has_its_key() -> None:
    assert keysym("a") == ord("a")
    assert keysym(" ") == 0x20
    assert keysym("é") == 0xE9  # Latin-1
    assert keysym("€") == 0x01000000 + ord("€")  # Unicode


async def test_the_pointer_glides_to_its_target_then_clicks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pointer, remote = portal(tmp_path, monkeypatch)
    await pointer.click(300, 150, (400, 200))
    assert len(remote.moves) == GLIDE_STEPS  # on le voit arriver, pas apparaître
    assert remote.moves[0] != (300.0, 150.0)
    assert remote.moves[-1] == (300.0, 150.0)
    assert remote.buttons == [True, False]
    await pointer.click(100, 50, (400, 200))  # le geste suivant part d'où il s'est arrêté
    assert remote.moves[GLIDE_STEPS][0] < 300.0
    assert remote.moves[-1] == (100.0, 50.0)


async def test_keys_stop_and_close_the_view_when_a_check_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pointer, remote = portal(tmp_path, monkeypatch)

    async def no() -> bool:
        return False

    steps: list[KeyStep] = [Key(SUPER), Pause(0), Check(no, "Pas de vue."), Text("lo")]
    with pytest.raises(UnavailableError, match="Pas de vue"):
        await pointer.keys(steps)
    assert remote.taps == [SUPER, ESCAPE]  # rien de tapé, la vue refermée


class FakeKeyboard:
    def __init__(self) -> None:
        self.steps: list[KeyStep] = []

    async def keys(self, steps: Sequence[KeyStep]) -> None:
        for step in steps:
            self.steps.append(step)
            if isinstance(step, Check) and not await step.passed():
                raise UnavailableError(step.failure)


def seeing(*answers: str) -> tuple[ModelRouter, list[str]]:
    """Muse Spark regarde l'écran et répond, dans l'ordre."""
    left = list(answers)
    asked: list[str] = []

    def script(request: LLMRequest) -> FakeReply:
        assert request.messages[-1].images  # il voit la capture
        asked.append(request.messages[-1].content)
        return FakeReply(text=left.pop(0))

    providers: dict[str, Any] = {"meta": FakeProvider("meta", script=script)}
    router = ModelRouter(providers, Catalog(), profile=lambda: "meta")
    return router, asked


async def screen() -> bytes:
    return window()


async def test_a_window_is_brought_back_through_the_activities_view(tmp_path: Path) -> None:
    router, asked = seeing("OUI", "Oui.")
    keyboard = FakeKeyboard()
    tool = BringWindow(router, keyboard, lambda: "en_ligne", screen)
    out = await tool.run(WindowArgs(application="LibreOffice Writer"), context(tmp_path))
    typed = [s for s in keyboard.steps if isinstance(s, (Key, Text))]
    assert typed == [Key(SUPER), Text("LibreOffice Writer"), Key(RETURN)]
    assert "est devant" in out.text
    assert "vue des activités" in asked[0]


async def test_nothing_is_typed_when_the_activities_view_did_not_open(tmp_path: Path) -> None:
    router, _ = seeing("NON")
    keyboard = FakeKeyboard()
    tool = BringWindow(router, keyboard, lambda: "en_ligne", screen)
    with pytest.raises(UnavailableError, match="rien n'a été tapé"):
        await tool.run(WindowArgs(application="Fichiers"), context(tmp_path))
    assert not [s for s in keyboard.steps if isinstance(s, Text)]


async def test_bringing_a_window_needs_the_screen_read_online(tmp_path: Path) -> None:
    router, _ = seeing()
    tool = BringWindow(router, FakeKeyboard(), lambda: "local", screen)
    with pytest.raises(InvalidInputError):
        await tool.run(WindowArgs(application="Fichiers"), context(tmp_path))
