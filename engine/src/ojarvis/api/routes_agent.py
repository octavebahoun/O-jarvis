"""Jarvis agit : plans à valider, journal des actions (lecture seule), outils, navigateur."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from ojarvis.agent.plans import PlanRecord
from ojarvis.api.deps import ServicesDep
from ojarvis.audit.journal import AuditRecord
from ojarvis.core.errors import NotFoundError

router = APIRouter(prefix="/v1")


# ---------------------------------------------------------------------- plans


class StepOut(BaseModel):
    position: int
    tool: str
    arguments: dict[str, Any]
    why: str
    level: int
    description: str
    status: str
    result: str | None
    error: str | None


class PlanOut(BaseModel):
    id: str
    conversation_id: str | None
    title: str
    request: str
    status: str
    max_level: int
    tainted: bool
    created_at: datetime
    updated_at: datetime
    decided_at: datetime | None
    result: str | None
    steps: list[StepOut]


def _plan(record: PlanRecord) -> PlanOut:
    return PlanOut(
        id=record.id,
        conversation_id=record.conversation_id,
        title=record.title,
        request=record.request,
        status=record.status,
        max_level=record.max_level,
        tainted=record.tainted,
        created_at=record.created_at,
        updated_at=record.updated_at,
        decided_at=record.decided_at,
        result=record.result,
        steps=[
            StepOut(
                position=s.position,
                tool=s.tool,
                arguments=s.arguments,
                why=s.why,
                level=s.level,
                description=s.description,
                status=s.status,
                # Le résultat brut d'une étape reste dans le moteur ; l'interface en voit un aperçu.
                result=s.result[:1500] if s.result else None,
                error=s.error,
            )
            for s in record.steps
        ],
    )


PlanFilter = Literal["pending", "active", "all"]
_FILTERS: dict[str, tuple[str, ...]] = {
    "pending": ("proposed",),
    "active": ("proposed", "approved", "running"),
    "all": (),
}


@router.get("/plans")
async def list_plans(
    services: ServicesDep,
    status: PlanFilter = "all",
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[PlanOut]:
    return [_plan(p) for p in await services.plans.list(_FILTERS[status], limit=limit)]


@router.get("/plans/{plan_id}")
async def get_plan(plan_id: str, services: ServicesDep) -> PlanOut:
    return _plan(await services.plans.get(plan_id))


class ApproveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Obligatoire pour un plan de niveau 3 (supprimer, installer, changer une permission).
    double_confirm: bool = False


@router.post("/plans/{plan_id}/approve")
async def approve_plan(plan_id: str, body: ApproveIn, services: ServicesDep) -> PlanOut:
    return _plan(await services.executor.approve(plan_id, double_confirm=body.double_confirm))


@router.post("/plans/{plan_id}/refuse")
async def refuse_plan(plan_id: str, services: ServicesDep) -> PlanOut:
    return _plan(await services.executor.refuse(plan_id))


@router.post("/plans/{plan_id}/stop")
async def stop_plan(plan_id: str, services: ServicesDep) -> PlanOut:
    """« Arrêter » : l'étape en cours se termine, les suivantes ne partent pas."""
    return _plan(await services.executor.stop(plan_id))


# ---------------------------------------------------------------------- journal des actions


class AuditOut(BaseModel):
    id: int
    ts: datetime
    actor: str
    action: str
    level: int
    summary: str
    details: dict[str, Any]
    hash: str


class AuditPageOut(BaseModel):
    items: list[AuditOut]
    total: int
    integrity_ok: bool | None
    broken_at: int | None


def _audit(record: AuditRecord) -> AuditOut:
    return AuditOut(
        id=record.id,
        ts=record.ts,
        actor=record.actor,
        action=record.action,
        level=record.level,
        summary=record.summary,
        details=record.details,
        hash=record.hash,
    )


@router.get("/audit")
async def audit_log(
    services: ServicesDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AuditPageOut:
    """Lecture seule : aucune route ne modifie ni n'efface le journal."""
    items, total = await services.journal.recent(limit=limit, offset=offset)
    report = services.journal.integrity
    return AuditPageOut(
        items=[_audit(r) for r in items],
        total=total,
        integrity_ok=report.ok if report else None,
        broken_at=report.broken_at if report else None,
    )


@router.post("/audit/verify")
async def verify_audit(services: ServicesDep) -> dict[str, Any]:
    report = await services.journal.verify()
    return {"ok": report.ok, "count": report.count, "broken_at": report.broken_at}


# ---------------------------------------------------------------------- outils


@router.get("/tools")
async def list_tools(services: ServicesDep) -> list[dict[str, Any]]:
    return services.tools.catalog()


# ---------------------------------------------------------------------- navigateur


class GrantOut(BaseModel):
    domain: str
    source: str
    imported_at: datetime
    count: int


class BrowserOut(BaseModel):
    sources: list[dict[str, str]]
    grants: list[GrantOut]
    login_domain: str | None


@router.get("/browser")
async def browser_state(services: ServicesDep) -> BrowserOut:
    access = services.browser
    return BrowserOut(
        sources=[{"id": s.id, "label": s.label} for s in await access.sources()],
        grants=[
            GrantOut(domain=g.domain, source=g.source, imported_at=g.imported_at, count=g.count)
            for g in await access.grants()
        ],
        login_domain=access.login_domain,
    )


@router.get("/browser/live")
async def browser_live(services: ServicesDep) -> Response:
    """La dernière image de la page de Jarvis (#34), lue par l'app sur cette machine."""
    live = services.browser.session.live
    frame = live.frame if live is not None else None
    if frame is None:
        raise NotFoundError("Le navigateur de Jarvis n'a encore rien affiché.")
    return Response(
        content=frame.jpeg,
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store", "X-Frame-Seq": str(frame.seq)},
    )


class ImportIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = Field(min_length=3, max_length=80)
    domain: str = Field(min_length=3, max_length=253)


@router.post("/browser/cookies")
async def import_cookies(body: ImportIn, services: ServicesDep) -> GrantOut:
    """Ton autorisation explicite pour un domaine : ses cookies rejoignent le coffre."""
    grant = await services.browser.import_cookies(body.source, body.domain)
    return GrantOut(
        domain=grant.domain, source=grant.source, imported_at=grant.imported_at, count=grant.count
    )


@router.delete("/browser/cookies/{domain}", status_code=204)
async def revoke_cookies(domain: str, services: ServicesDep) -> Response:
    await services.browser.revoke(domain)
    return Response(status_code=204)


class LoginIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=3, max_length=2000)


@router.post("/browser/login")
async def start_login(body: LoginIn, services: ServicesDep) -> dict[str, str]:
    """Ouvre une fenêtre visible du navigateur de Jarvis : tu t'y connectes toi-même."""
    return {"domain": await services.browser.login_start(body.url)}


@router.post("/browser/login/finish")
async def finish_login(services: ServicesDep) -> GrantOut | None:
    grant = await services.browser.login_finish()
    if grant is None:
        return None
    return GrantOut(
        domain=grant.domain, source=grant.source, imported_at=grant.imported_at, count=grant.count
    )
