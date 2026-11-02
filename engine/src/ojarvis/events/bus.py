"""Bus d'événements interne : les modules communiquent par événements typés.

- `publish` enregistre l'événement en base (sauf `persist=False`, pour les flux rapides
  comme le niveau du micro), puis le distribue.
- `subscribe` appelle un gestionnaire asynchrone ; une erreur dans un gestionnaire est
  journalisée sans casser le bus.
- `stream` fournit une file bornée (l'interface, via WebSocket) : si le client est lent,
  les plus anciens événements sont jetés et comptés.
"""

from __future__ import annotations

import asyncio
import contextlib
import fnmatch
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from ojarvis.core.clock import utc_now
from ojarvis.core.context import current_correlation_id
from ojarvis.core.logs import get_logger
from ojarvis.security.redaction import REDACTOR

log = get_logger(__name__)


class Event(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int | None = None
    type: str
    ts: datetime
    source: str
    correlation_id: str | None = None
    payload: dict[str, Any]


Handler = Callable[[Event], Awaitable[None]]
Persister = Callable[[Event], Awaitable[int]]


def matches(pattern: str, event_type: str) -> bool:
    return pattern == "*" or fnmatch.fnmatchcase(event_type, pattern)


@dataclass(eq=False)
class _Subscriber:
    pattern: str
    handler: Handler | None = None
    queue: asyncio.Queue[Event] | None = None
    dropped: int = 0


@dataclass
class BusStats:
    published: int = 0
    persisted: int = 0
    dropped: int = 0
    handler_errors: int = 0
    subscribers: int = 0
    #: Les flux ouverts (l'app, un client SSE), sans les abonnés internes du moteur.
    streams: int = 0
    by_type: dict[str, int] = field(default_factory=dict)


class Subscription:
    def __init__(self, bus: EventBus, subscriber: _Subscriber) -> None:
        self._bus = bus
        self._subscriber = subscriber

    def close(self) -> None:
        self._bus._remove(self._subscriber)


class EventBus:
    def __init__(self, persister: Persister | None = None, *, queue_size: int = 512) -> None:
        self._persister = persister
        self._queue_size = queue_size
        self._subscribers: list[_Subscriber] = []
        self._tasks: set[asyncio.Task[None]] = set()
        self.stats = BusStats()

    async def publish(
        self,
        event_type: str,
        payload: Mapping[str, Any] | None = None,
        *,
        source: str = "engine",
        persist: bool = True,
    ) -> Event:
        event = Event(
            type=event_type,
            ts=utc_now(),
            source=source,
            correlation_id=current_correlation_id(),
            payload=REDACTOR.redact_obj(dict(payload or {})),
        )
        if persist and self._persister is not None:
            try:
                event = event.model_copy(update={"id": await self._persister(event)})
                self.stats.persisted += 1
            except Exception:
                log.exception("event_persist_failed", event_type=event_type)
        self.stats.published += 1
        self.stats.by_type[event_type] = self.stats.by_type.get(event_type, 0) + 1
        self._dispatch(event)
        return event

    def _dispatch(self, event: Event) -> None:
        for sub in list(self._subscribers):
            if not matches(sub.pattern, event.type):
                continue
            if sub.handler is not None:
                task = asyncio.create_task(self._call(sub.handler, event))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)
            elif sub.queue is not None:
                if sub.queue.full():
                    with contextlib.suppress(asyncio.QueueEmpty):
                        sub.queue.get_nowait()
                    sub.dropped += 1
                    self.stats.dropped += 1
                sub.queue.put_nowait(event)

    async def _call(self, handler: Handler, event: Event) -> None:
        try:
            await handler(event)
        except Exception:
            self.stats.handler_errors += 1
            log.exception("event_handler_failed", event_type=event.type)

    def subscribe(self, pattern: str, handler: Handler) -> Subscription:
        subscriber = _Subscriber(pattern=pattern, handler=handler)
        self._subscribers.append(subscriber)
        self._count()
        return Subscription(self, subscriber)

    @contextlib.asynccontextmanager
    async def stream(self, pattern: str = "*") -> AsyncIterator[asyncio.Queue[Event]]:
        queue: asyncio.Queue[Event] = asyncio.Queue(self._queue_size)
        subscriber = _Subscriber(pattern=pattern, queue=queue)
        self._subscribers.append(subscriber)
        self._count()
        try:
            yield queue
        finally:
            self._remove(subscriber)

    def _count(self) -> None:
        self.stats.subscribers = len(self._subscribers)
        self.stats.streams = sum(1 for sub in self._subscribers if sub.queue is not None)

    def _remove(self, subscriber: _Subscriber) -> None:
        if subscriber in self._subscribers:
            self._subscribers.remove(subscriber)
        self._count()

    async def drain(self) -> None:
        """Attend la fin des gestionnaires en cours (arrêt propre, tests).

        On n'attend que les tâches encore en cours : une tâche finie reste dans l'ensemble
        jusqu'à ce que la boucle exécute son rappel de fin, et `gather` sur des tâches
        finies rend la main sans passer par la boucle (on tournerait sans fin).
        """
        while pending := [task for task in self._tasks if not task.done()]:
            await asyncio.gather(*pending, return_exceptions=True)
