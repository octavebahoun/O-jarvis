"""Valider sans revenir au terminal : une notification GNOME « Valider · Refuser ».

Le 2/11/2026, au tournage : Jarvis ramenait LibreOffice devant le terminal, et la question
suivante attendait dans le terminal désormais caché. Quand un plan attend, le terminal envoie
aussi une notification : on y clique, ou l'on répond au clavier, la première réponse compte.
Au niveau 3 (supprimer, payer), la notification n'a pas de bouton : on confirme dans le
terminal, en tapant « oui ».
"""

from __future__ import annotations

import contextlib
import os
import select
import sys
import threading
from collections.abc import Callable
from typing import Protocol, TextIO

from jeepney.low_level import HeaderFields

#: Ce que renvoie un bouton : la réponse qu'on aurait tapée.
BUTTONS = {"oui": "o", "non": "n"}
BUS = "org.freedesktop.Notifications"
PATH = "/org/freedesktop/Notifications"


class Notice(Protocol):
    def answer(self) -> str | None: ...

    def close(self) -> None: ...


#: (titre, texte, avec les boutons) → la notification envoyée, ou None.
NoticeFactory = Callable[[str, str, bool], Notice | None]


class DesktopNotice:
    """Une notification de GNOME, envoyée par D-Bus (`jeepney`) ; un fil écoute le bouton
    cliqué. (`notify-send` d'Ubuntu 26.04 refusait ses boutons, que GNOME accepte.)"""

    def __init__(self, title: str, body: str, *, buttons: bool) -> None:
        from jeepney import DBusAddress, MatchRule, new_method_call  # noqa: PLC0415
        from jeepney.bus_messages import message_bus  # noqa: PLC0415
        from jeepney.io.blocking import open_dbus_connection  # noqa: PLC0415

        self._address = DBusAddress(PATH, BUS, BUS)
        self._call = new_method_call
        self._connection = open_dbus_connection(bus="SESSION")
        self._rule = MatchRule(type="signal", interface=BUS, path=PATH)
        self._connection.send_and_get_reply(message_bus.AddMatch(self._rule), timeout=5)
        actions = ["oui", "Valider", "non", "Refuser"] if buttons else []
        hints = {"urgency": ("y", 2), "resident": ("b", True)}
        reply = self._connection.send_and_get_reply(
            new_method_call(
                self._address,
                "Notify",
                "susssasa{sv}i",
                ("O-jarvis", 0, "dialog-question", title, body, actions, hints, 0),
            ),
            timeout=5,
        )
        self._id = int(reply.body[0])
        self._answer: str | None = None
        self._stop = threading.Event()
        threading.Thread(target=self._listen, name="notification", daemon=True).start()

    def _listen(self) -> None:
        with self._connection.filter(self._rule) as signals:
            while not self._stop.is_set():
                try:
                    message = self._connection.recv_until_filtered(signals, timeout=0.5)
                except TimeoutError:
                    continue
                except OSError:
                    return
                member = message.header.fields.get(HeaderFields.member)
                if not message.body or int(message.body[0]) != self._id:
                    continue
                if member == "ActionInvoked" and str(message.body[1]) in BUTTONS:
                    self._answer = BUTTONS[str(message.body[1])]
                if member in ("ActionInvoked", "NotificationClosed"):
                    return

    def answer(self) -> str | None:
        return self._answer

    def close(self) -> None:
        self._stop.set()
        with contextlib.suppress(OSError, TimeoutError):
            self._connection.send_and_get_reply(
                self._call(self._address, "CloseNotification", "u", (self._id,)), timeout=5
            )
        with contextlib.suppress(OSError):
            self._connection.close()


def desktop_notices() -> NoticeFactory | None:
    """Les notifications du bureau, si l'on peut en envoyer et lire le clavier en même temps."""
    if not sys.stdin.isatty() or not os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
        return None

    def send(title: str, body: str, buttons: bool) -> Notice | None:
        try:
            return DesktopNotice(title, body, buttons=buttons)
        except Exception:  # pas de bureau, ou D-Bus muet : le clavier suffit
            return None

    return send


def ask_or_notice(
    prompt: str,
    notice: Notice,
    *,
    stdin: TextIO | None = None,
    out: TextIO | None = None,
    poll_s: float = 0.2,
) -> str:
    """La réponse tapée au clavier ou cliquée dans la notification, la première des deux."""
    stdin = stdin or sys.stdin
    out = out or sys.stdout
    out.write(prompt)
    out.flush()
    try:
        while True:
            ready, _, _ = select.select([stdin], [], [], poll_s)
            if ready:
                line = stdin.readline()
                if not line:
                    raise EOFError
                return line.rstrip("\n")
            answer = notice.answer()
            if answer is not None:
                out.write(f"{answer}  (depuis la notification)\n")
                out.flush()
                return answer
    finally:
        notice.close()
