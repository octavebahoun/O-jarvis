"""Valider sans revenir au terminal : la notification « Valider · Refuser » répond à la place
du clavier, la première réponse compte ; au niveau 3, elle n'a pas de bouton."""

from __future__ import annotations

import io
import os

from ojarvis_shell.shell.notify import Notice, ask_or_notice
from tests.shell.fake_engine import DONE, START, FakeEngine, plan, session, sse


class FakeNotice:
    def __init__(self, answer: str | None = None, after: int = 0) -> None:
        self._answer = answer
        self._after = after
        self.closed = False

    def answer(self) -> str | None:
        if self._after:
            self._after -= 1
            return None
        return self._answer

    def close(self) -> None:
        self.closed = True


def keyboard(typed: str = "") -> io.TextIOWrapper:
    """Une entrée qu'on peut attendre avec select (un tube), avec ce qui y a été tapé."""
    read, write = os.pipe()
    os.write(write, typed.encode())
    if typed:
        os.close(write)  # sans rien de tapé, le tube reste ouvert : on attend
    return io.TextIOWrapper(io.FileIO(read, closefd=True))


def test_a_click_in_the_notification_answers_for_the_keyboard() -> None:
    notice = FakeNotice("o", after=2)
    out = io.StringIO()
    answer = ask_or_notice("Valider ? ", notice, stdin=keyboard(), out=out, poll_s=0.01)
    assert answer == "o"
    assert notice.closed
    assert out.getvalue() == "Valider ? o  (depuis la notification)\n"


def test_a_typed_answer_wins_and_closes_the_notification() -> None:
    notice = FakeNotice(None)
    answer = ask_or_notice("Valider ? ", notice, stdin=keyboard("n\n"), out=io.StringIO())
    assert answer == "n"
    assert notice.closed


def test_a_waiting_plan_sends_its_notification() -> None:
    sent: list[tuple[str, str, bool]] = []

    def factory(title: str, body: str, buttons: bool) -> Notice:
        sent.append((title, body, buttons))
        return FakeNotice("o")

    engine = FakeEngine([sse(START, ("plan", plan(2)), DONE), sse(START, DONE)])
    client, _out = session(engine, [])
    client._notice = factory
    client._race = lambda prompt, notice: notice.answer() or ""
    client.send("Libère de la place.")
    assert sent == [
        ("Jarvis attend ta validation", "Nettoyer : Lancer « docker image prune -f »", True)
    ]
    assert "/v1/plans/p1/approve" in [p for _m, p, _b in engine.requests]


def test_a_destruction_is_confirmed_in_the_terminal_only() -> None:
    sent: list[tuple[str, str, bool]] = []

    def factory(title: str, body: str, buttons: bool) -> Notice:
        sent.append((title, body, buttons))
        return FakeNotice(None)

    engine = FakeEngine([sse(START, ("plan", plan(3)), DONE)])
    client, _out = session(engine, [])
    client._notice = factory
    client._race = lambda prompt, notice: notice.answer() or "o"
    client.send("Supprime les images.")
    assert sent[0][2] is False  # pas de bouton : on tape « oui » dans le terminal
    assert "Niveau 3 : confirme dans le terminal" in sent[0][1]
    assert "/v1/plans/p1/approve" not in [p for _m, p, _b in engine.requests]
