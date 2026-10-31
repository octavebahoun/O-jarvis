"""Le pont des actions vers ton onglet : un outil y dépose une action (cliquer, remplir,
ouvrir), l'hôte de l'extension la prend, le panneau la fait dans l'onglet et rend le résultat.

L'hôte attend les actions par une requête longue (`next`) ; tant qu'aucun panneau n'est
ouvert, une action échoue tout de suite au lieu d'attendre dans le vide.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from ojarvis.core.errors import UnavailableError

#: Un panneau qui n'est pas repassé chercher d'actions depuis ce temps est considéré fermé.
PANEL_GONE_S = 45.0
#: Temps laissé au panneau pour faire une action (un clic peut charger une page).
ACTION_TIMEOUT_S = 60.0


@dataclass(frozen=True, slots=True)
class TabAction:
    kind: str  # cliquer | remplir | ouvrir
    args: dict[str, Any]
    level: int
    id: str = field(default_factory=lambda: uuid.uuid4().hex)


@dataclass(frozen=True, slots=True)
class TabResult:
    ok: bool
    text: str


class ChromeBridge:
    def __init__(self, *, clock: Any = time.monotonic) -> None:
        self._clock = clock
        self._queue: asyncio.Queue[TabAction] = asyncio.Queue()
        self._waiting: dict[str, asyncio.Future[TabResult]] = {}
        self._pollers = 0
        self._seen = float("-inf")

    def panel_open(self) -> bool:
        return self._pollers > 0 or self._clock() - self._seen < PANEL_GONE_S

    async def run(self, action: TabAction, *, timeout_s: float = ACTION_TIMEOUT_S) -> TabResult:
        """Fait faire l'action par le panneau et attend son résultat."""
        if not self.panel_open():
            raise UnavailableError(
                "Le panneau de Jarvis est fermé dans Chrome : ouvre-le pour que j'agisse "
                "dans ton onglet."
            )
        future: asyncio.Future[TabResult] = asyncio.get_running_loop().create_future()
        self._waiting[action.id] = future
        await self._queue.put(action)
        try:
            return await asyncio.wait_for(future, timeout_s)
        except TimeoutError as error:
            raise UnavailableError("L'onglet n'a pas répondu à temps.") from error
        finally:
            self._waiting.pop(action.id, None)

    async def next(self, wait_s: float) -> TabAction | None:
        """L'action suivante pour le panneau ; None si rien n'arrive pendant `wait_s`."""
        self._pollers += 1
        try:
            while True:
                action = await asyncio.wait_for(self._queue.get(), wait_s)
                if action.id in self._waiting:  # sinon, plus personne ne l'attend
                    return action
        except TimeoutError:
            return None
        finally:
            self._pollers -= 1
            self._seen = self._clock()

    def resolve(self, action_id: str, result: TabResult) -> bool:
        future = self._waiting.get(action_id)
        if future is None or future.done():
            return False
        future.set_result(result)
        return True
