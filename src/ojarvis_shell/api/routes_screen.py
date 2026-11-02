"""Les routes de l'écran, construites pendant le hackathon Meta (O-jarvis Shell) : la capture
annotée d'un élément trouvé (#48) et le partage d'écran en direct (#50)."""

from __future__ import annotations

from typing import Any

import anyio
from fastapi import APIRouter, Response
from ojarvis.api.deps import ServicesDep
from ojarvis.core.errors import ConflictError, NotFoundError
from ojarvis.services import Services
from pydantic import BaseModel

router = APIRouter(prefix="/v1")


def shell_state(services: Services) -> Any:
    """Les objets de l'extension (repères, écran partagé), assemblés avec le moteur."""
    if services.added is None:
        raise NotFoundError("O-jarvis Shell n'est pas installé.")
    return services.added.state


@router.get("/desktop/marks/{mark_id}")
async def desktop_mark(mark_id: str, services: ServicesDep) -> Response:
    """La capture annotée d'un élément trouvé à l'écran (#48) : la carte du plan la montre."""
    path = shell_state(services).marks.image(mark_id)
    return Response(
        content=await anyio.Path(path).read_bytes(),
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store"},
    )


class ShareOut(BaseModel):
    active: bool
    frames: int
    since: float | None


@router.get("/desktop/share")
async def share_state(services: ServicesDep) -> ShareOut:
    return ShareOut(**shell_state(services).share.state())


@router.post("/desktop/share/start")
async def share_start(services: ServicesDep) -> ShareOut:
    """Partager l'écran en direct : GNOME demande ton accord la première fois."""
    if not services.preferences.os_control_enabled:
        raise ConflictError(
            "Le partage d'écran sert au Contrôle du PC : active-le d'abord dans Réglages.",
            code="os_control_disabled",
        )
    await shell_state(services).share.start()
    return ShareOut(**shell_state(services).share.state())


@router.post("/desktop/share/stop")
async def share_stop(services: ServicesDep) -> ShareOut:
    await shell_state(services).share.stop()
    return ShareOut(**shell_state(services).share.state())


@router.get("/desktop/share/frame")
async def share_frame(services: ServicesDep) -> Response:
    """La dernière image de l'écran partagé, pour l'aperçu de l'app (sur cette machine)."""
    frame = shell_state(services).share.frame()
    if frame is None:
        raise NotFoundError("L'écran n'est pas partagé.")
    return Response(content=frame, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@router.post("/desktop/pointer/authorize")
async def pointer_authorize(services: ServicesDep) -> dict[str, bool]:
    """L'accueil de l'app : demander tout de suite à GNOME l'accord de contrôler le pointeur
    et le clavier (une session ouverte puis refermée, sans aucun geste), pour qu'il ne
    surgisse pas au premier clic."""
    await shell_state(services).pointer.keys([])
    return {"authorized": True}
