"""Le panneau de Jarvis dans Chrome, par son hôte : il prend les actions à faire dans ton
onglet (requête longue) et rend leur résultat (voir `ojarvis.chrome.bridge`)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from ojarvis.api.deps import ServicesDep
from ojarvis.chrome.bridge import TabResult
from ojarvis.core.errors import NotFoundError

router = APIRouter(prefix="/v1/chrome", tags=["chrome"])


@router.get("/actions/next", response_model=None)
async def next_action(
    services: ServicesDep, wait: float = Query(default=20.0, ge=0.0, le=30.0)
) -> dict[str, Any] | Response:
    action = await services.chrome.next(wait)
    if action is None:
        return Response(status_code=204)
    return {"id": action.id, "kind": action.kind, "args": action.args, "level": action.level}


class ResultIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool
    text: str = Field(default="", max_length=20_000)


@router.post("/actions/{action_id}", status_code=204)
async def action_result(action_id: str, body: ResultIn, services: ServicesDep) -> Response:
    if not services.chrome.resolve(action_id, TabResult(body.ok, body.text)):
        raise NotFoundError("Cette action n'attend plus de résultat.")
    return Response(status_code=204)
