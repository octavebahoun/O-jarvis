"""Les serveurs lancés par le mode shell, et les ports où ils écoutent.

Jarvis regarde sans te demander une page servie par un serveur qu'il a lui-même lancé en
arrière-plan (ta validation a déjà eu lieu au lancement). Une page d'un autre port de ta
machine attend ta validation : rien ne dit ce qu'elle montre.

Un serveur se reconnaît à sa session : `start_new_session` en ouvre une à chaque lancement,
et tous ses processus (npm, node, vite…) en héritent.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from pathlib import Path

_USERS = re.compile(r"pid=(\d+)")


def listening_pids(port: int) -> set[int]:
    """Les processus qui écoutent sur ce port TCP (selon `ss`, sans droits particuliers)."""
    try:
        output = subprocess.run(  # noqa: S603 - outil du système, arguments fixes
            ["ss", "-ltnpH", f"sport = :{port}"],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return set()
    return {int(pid) for pid in _USERS.findall(output)}


def session_of(pid: int, proc: Path = Path("/proc")) -> int | None:
    """La session d'un processus (6ᵉ champ de /proc/PID/stat, après le nom entre parenthèses)."""
    try:
        stat = (proc / str(pid) / "stat").read_text()
    except OSError:
        return None
    fields = stat.rsplit(")", 1)[-1].split()
    try:
        return int(fields[3])  # état, parent, groupe, session
    except (IndexError, ValueError):
        return None


class BackgroundSessions:
    """Les sessions des commandes lancées en arrière-plan par ce moteur."""

    def __init__(
        self,
        pids_on_port: Callable[[int], set[int]] = listening_pids,
        session: Callable[[int], int | None] = session_of,
    ) -> None:
        self._sessions: set[int] = set()
        self._pids_on_port = pids_on_port
        self._session = session

    def add(self, leader: int) -> None:
        """`leader` : le bash lancé avec `start_new_session`, dont le PID est la session."""
        self._sessions.add(leader)

    def owns_port(self, port: int) -> bool:
        return any(self._session(pid) in self._sessions for pid in self._pids_on_port(port))
