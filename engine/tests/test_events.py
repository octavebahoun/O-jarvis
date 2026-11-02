from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from ojarvis.db.engine import Database
from ojarvis.db.migrate import upgrade_to_head
from ojarvis.events.bus import Event, EventBus, matches
from ojarvis.events.store import EventStore


@pytest.mark.parametrize(
    ("pattern", "event_type", "expected"),
    [
        ("*", "trace.span", True),
        ("trace.*", "trace.span", True),
        ("trace.*", "secret.updated", False),
        ("secret.updated", "secret.updated", True),
        ("secret.updated", "secret.updated.extra", False),
    ],
)
def test_matches(pattern: str, event_type: str, expected: bool) -> None:
    assert matches(pattern, event_type) is expected


async def test_handlers_receive_matching_events() -> None:
    bus = EventBus()
    seen: list[str] = []

    async def handler(event: Event) -> None:
        seen.append(event.type)

    bus.subscribe("trace.*", handler)
    await bus.publish("trace.span", {"x": 1})
    await bus.publish("secret.updated", {})
    await bus.drain()
    assert seen == ["trace.span"]
    assert bus.stats.published == 2


async def test_failing_handler_does_not_break_the_bus() -> None:
    bus = EventBus()
    seen: list[str] = []

    async def broken(_: Event) -> None:
        raise RuntimeError("boum")

    async def fine(event: Event) -> None:
        seen.append(event.type)

    bus.subscribe("*", broken)
    bus.subscribe("*", fine)
    await bus.publish("a.b")
    await bus.drain()
    assert seen == ["a.b"]
    assert bus.stats.handler_errors == 1


async def test_unsubscribe() -> None:
    bus = EventBus()
    seen: list[str] = []

    async def handler(event: Event) -> None:
        seen.append(event.type)

    subscription = bus.subscribe("*", handler)
    subscription.close()
    await bus.publish("a.b")
    await bus.drain()
    assert seen == []
    assert bus.stats.subscribers == 0


async def test_stream_drops_oldest_when_full() -> None:
    bus = EventBus(queue_size=2)
    async with bus.stream("*") as queue:
        for i in range(4):
            await bus.publish("n", {"i": i}, persist=False)
        assert [queue.get_nowait().payload["i"] for _ in range(2)] == [2, 3]
    assert bus.stats.dropped == 2
    assert bus.stats.subscribers == 0


async def test_only_open_streams_count_as_clients() -> None:
    """Le terminal se fie à ce nombre pour savoir si l'app suit le moteur : les abonnés
    internes (planificateur, anticipation) n'en sont pas."""
    bus = EventBus()

    async def handler(event: Event) -> None:
        del event

    bus.subscribe("*", handler)
    assert (bus.stats.subscribers, bus.stats.streams) == (1, 0)
    async with bus.stream("*"):
        assert (bus.stats.subscribers, bus.stats.streams) == (2, 1)
    assert (bus.stats.subscribers, bus.stats.streams) == (1, 0)


async def test_payload_is_redacted() -> None:
    bus = EventBus()
    async with bus.stream("*") as queue:
        await bus.publish("x", {"auth": "Bearer abcdefghijklmnopqrstu"})
        event = await asyncio.wait_for(queue.get(), 1)
    assert "abcdefghijklmnopqrstu" not in str(event.payload)


async def test_persisted_events_have_ids_and_can_be_listed(tmp_path: Path) -> None:
    db_path = tmp_path / "e.db"
    upgrade_to_head(db_path)
    store = EventStore(Database(db_path))
    bus = EventBus(store.persist)
    first = await bus.publish("engine.started", {"v": 1})
    await bus.publish("audio.level", {"rms": 0.2}, persist=False)
    second = await bus.publish("secret.updated", {"name": "X"})
    assert first.id is not None
    assert second.id is not None
    assert second.id > first.id
    recent = await store.recent(limit=10)
    assert [e.type for e in recent] == ["secret.updated", "engine.started"]
    only = await store.recent(type_prefix="engine.")
    assert [e.type for e in only] == ["engine.started"]


async def test_persist_failure_still_dispatches() -> None:
    async def failing(_: Event) -> int:
        raise RuntimeError("base indisponible")

    bus = EventBus(failing)
    async with bus.stream("*") as queue:
        event = await bus.publish("x.y")
        assert event.id is None
        assert (await asyncio.wait_for(queue.get(), 1)).type == "x.y"


async def test_drain_returns_when_a_handler_just_finished() -> None:
    """Régression : une tâche finie dont le rappel de fin n'a pas encore tourné."""
    bus = EventBus()
    seen: list[str] = []

    async def handler(event: Event) -> None:
        seen.append(event.type)

    bus.subscribe("*", handler)
    await bus.publish("test.fini", {}, persist=False)
    await asyncio.sleep(0)  # la tâche du gestionnaire s'exécute ; son rappel attend encore
    await asyncio.wait_for(bus.drain(), timeout=1)
    assert seen == ["test.fini"]
