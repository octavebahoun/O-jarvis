"""Santé, état du moteur, préférences, traces, événements et diagnostics."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Any

import anyio.to_thread
from fastapi import APIRouter, Query

from ojarvis import __version__
from ojarvis.api.deps import ServicesDep
from ojarvis.api.schemas import (
    EventOut,
    PerfReportIn,
    PerfReportOut,
    SpanOut,
    StatusOut,
    TraceDetailOut,
    TraceSummaryOut,
)
from ojarvis.core.clock import system_timezone_name, utc_now
from ojarvis.core.errors import NotFoundError
from ojarvis.settings_store import Preferences

public = APIRouter()
router = APIRouter(prefix="/v1")


@public.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/status")
async def status(services: ServicesDep) -> StatusOut:
    secrets = await anyio.to_thread.run_sync(services.secrets.status)
    stats = services.bus.stats
    return StatusOut(
        version=__version__,
        dev_mode=services.config.dev_mode,
        started_at=services.started_at,
        uptime_s=round((utc_now() - services.started_at).total_seconds(), 1),
        keyring=services.secrets.backend_name,
        keyring_error=services.keyring_error,
        secrets_configured=sum(1 for s in secrets if s.configured),
        secrets_total=len(secrets),
        events_published=stats.published,
        events_dropped=stats.dropped,
        stream_clients=stats.streams,
        timezone=system_timezone_name(),
    )


@router.get("/settings")
async def get_settings(services: ServicesDep) -> Preferences:
    return await services.settings.get()


#: Réglages qui changent ce que Jarvis a le droit de faire : écrits au journal des actions.
PERMISSION_SETTINGS: dict[str, Callable[[Any], str]] = {
    "os_control_enabled": lambda on: "Contrôle du PC activé" if on else "Contrôle du PC désactivé",
    "screen_vision": lambda mode: (
        "Lecture de l'écran par un modèle en ligne"
        if mode == "en_ligne"
        else "Lecture de l'écran sur ce PC"
    ),
}


@router.patch("/settings")
async def patch_settings(changes: dict[str, Any], services: ServicesDep) -> Preferences:
    updated, changed = await services.settings.update(changes)
    services.set_preferences(updated)
    for key in changed:
        if key in PERMISSION_SETTINGS:
            value = getattr(updated, key)
            await services.journal.append(
                actor="utilisateur",
                action=f"reglage.{key}",
                level=3,
                summary=PERMISSION_SETTINGS[key](value),
                details={key: value},
            )
    if changed:
        await services.bus.publish(
            "settings.changed",
            {"keys": changed, "values": {k: getattr(updated, k) for k in changed}},
        )
    return updated


@router.get("/traces")
async def list_traces(
    services: ServicesDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    before: datetime | None = None,
) -> list[TraceSummaryOut]:
    rows = await services.traces.recent(limit=limit, before=before)
    return [TraceSummaryOut.model_validate(r) for r in rows]


@router.get("/traces/{trace_id}")
async def get_trace(trace_id: str, services: ServicesDep) -> TraceDetailOut:
    found = await services.traces.get(trace_id)
    if found is None:
        raise NotFoundError("Trace introuvable.")
    trace, spans = found
    return TraceDetailOut(
        id=trace.id,
        name=trace.name,
        kind=trace.kind,
        status=trace.status,
        started_at=trace.started_at,
        ended_at=trace.ended_at,
        duration_ms=trace.duration_ms,
        correlation_id=trace.correlation_id,
        error=trace.error,
        meta=trace.meta,
        spans=[SpanOut.model_validate(s) for s in spans],
    )


@router.get("/events")
async def list_events(
    services: ServicesDep,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    type_prefix: Annotated[str | None, Query(max_length=96)] = None,
) -> list[EventOut]:
    events = await services.events.recent(limit=limit, type_prefix=type_prefix)
    return [EventOut.model_validate(e.model_dump()) for e in events]


@router.post("/diagnostics/perf")
async def perf_report(report: PerfReportIn, services: ServicesDep) -> PerfReportOut:
    """Mesure de fluidité envoyée par l'interface (sphère), gardée comme preuve."""
    folder = services.config.paths.state_dir / "perf"
    stamp = utc_now().strftime("%Y%m%dT%H%M%SZ")
    target = folder / f"perf-{stamp}.json"
    payload = report.model_dump()

    def write() -> None:
        folder.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    await anyio.to_thread.run_sync(write)
    event = await services.bus.publish("diagnostics.perf", payload, source="ui")
    return PerfReportOut(saved_as=str(target), event_id=event.id)
