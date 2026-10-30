"""Cliquer à l'écran (#48) par le portail de contrôle à distance de GNOME.

Sous Wayland, une application ne pilote pas la souris : elle passe par le portail
`org.freedesktop.portal.RemoteDesktop` (D-Bus). La première fois, GNOME te demande
l'autorisation ; elle est retenue (jeton de reprise, lisible par toi seul) jusqu'à ce que tu
la retires dans les Paramètres. Sans ton accord, aucun clic n'est possible.

Chaque clic ouvre une session, déplace le pointeur au centre de l'élément (coordonnées
absolues du flux de l'écran), clique, puis ferme la session : l'indicateur de partage de
GNOME ne reste pas allumé. Ni `ydotool`, ni démon lancé en root.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from ojarvis.core.errors import UnavailableError
from ojarvis.core.logs import get_logger

log = get_logger(__name__)

PORTAL = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
POINTER = 2  # type de périphérique du portail
MONITOR = 1  # type de source du portail
PERSIST_UNTIL_REVOKED = 2
BTN_LEFT = 272  # code evdev du bouton gauche
#: Le temps laissé pour répondre à la fenêtre d'autorisation de GNOME.
ANSWER_TIMEOUT_S = 90.0
REQUEST_TIMEOUT_S = 15.0


@dataclass(frozen=True, slots=True)
class Stream:
    """L'écran partagé par le portail : son identifiant et sa taille (pixels logiques)."""

    node: int
    width: int
    height: int


class Pointer(Protocol):
    async def click(self, x: int, y: int, image_size: tuple[int, int]) -> None: ...


def to_stream(x: int, y: int, image_size: tuple[int, int], stream: Stream) -> tuple[float, float]:
    """Un point de la capture → le même point sur l'écran partagé (échelle comprise)."""
    width, height = image_size
    if not (width and height and stream.width and stream.height):
        raise UnavailableError("Taille de l'écran inconnue : clic annulé.")
    if abs(width / height - stream.width / stream.height) > 0.02:
        raise UnavailableError(
            "La capture et l'écran partagé n'ont pas la même forme (plusieurs écrans ?) : "
            "le clic est annulé plutôt que de tomber à côté. Un seul écran pour l'instant."
        )
    return x * stream.width / width, y * stream.height / height


def _variant(results: dict[str, Any], key: str, default: Any = None) -> Any:
    value = results.get(key)
    return value[1] if isinstance(value, tuple) and len(value) == 2 else default


class PortalPointer:
    """Le clic par le portail, avec `jeepney` (déjà utilisé pour les captures d'écran)."""

    def __init__(self, token_file: Path) -> None:
        self._token_file = token_file

    def _restore_token(self) -> str | None:
        try:
            return self._token_file.read_text(encoding="utf-8").strip() or None
        except OSError:
            return None

    def _save_token(self, token: str) -> None:
        self._token_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._token_file.write_text(token, encoding="utf-8")
        self._token_file.chmod(0o600)

    async def click(self, x: int, y: int, image_size: tuple[int, int]) -> None:
        from jeepney.io.asyncio import open_dbus_router  # noqa: PLC0415

        try:
            async with open_dbus_router(bus="SESSION") as router:
                await self._click(router, x, y, image_size)
        except UnavailableError:
            raise
        except Exception as exc:
            log.warning("portal_click_failed", error=type(exc).__name__)
            raise UnavailableError(
                f"Clic impossible par le portail de GNOME ({type(exc).__name__})."
            ) from exc

    async def _click(self, router: Any, x: int, y: int, image_size: tuple[int, int]) -> None:
        from jeepney import DBusAddress, new_method_call  # noqa: PLC0415

        remote = DBusAddress(PORTAL_PATH, PORTAL, "org.freedesktop.portal.RemoteDesktop")
        screencast = DBusAddress(PORTAL_PATH, PORTAL, "org.freedesktop.portal.ScreenCast")
        request = _Requests(router)
        created = await request.call(
            lambda token: new_method_call(
                remote,
                "CreateSession",
                "a{sv}",
                ({"handle_token": ("s", token), "session_handle_token": ("s", token)},),
            )
        )
        session = str(_variant(created, "session_handle", ""))
        if not session:
            raise UnavailableError("GNOME n'a pas ouvert de session de contrôle.")
        try:
            devices: dict[str, Any] = {
                "types": ("u", POINTER),
                "persist_mode": ("u", PERSIST_UNTIL_REVOKED),
            }
            token = self._restore_token()
            if token:
                devices["restore_token"] = ("s", token)
            await request.call(
                lambda handle: new_method_call(
                    remote,
                    "SelectDevices",
                    "oa{sv}",
                    (session, {**devices, "handle_token": ("s", handle)}),
                )
            )
            # Des coordonnées absolues demandent l'écran partagé (un seul, sans curseur).
            await request.call(
                lambda handle: new_method_call(
                    screencast,
                    "SelectSources",
                    "oa{sv}",
                    (
                        session,
                        {
                            "types": ("u", MONITOR),
                            "multiple": ("b", False),
                            "handle_token": ("s", handle),
                        },
                    ),
                )
            )
            started = await request.call(
                lambda handle: new_method_call(
                    remote,
                    "Start",
                    "osa{sv}",
                    (session, "", {"handle_token": ("s", handle)}),
                ),
                wait_s=ANSWER_TIMEOUT_S,
                refused=(
                    "Clic refusé : GNOME n'a pas reçu ton autorisation de contrôler le "
                    "pointeur. Rien n'a été cliqué."
                ),
            )
            new_token = _variant(started, "restore_token")
            if new_token:
                self._save_token(str(new_token))
            streams = _variant(started, "streams", []) or []
            if not streams:
                raise UnavailableError("GNOME n'a partagé aucun écran : clic annulé.")
            node, properties = streams[0]
            width, height = _variant(properties, "size", (0, 0))
            stream = Stream(int(node), int(width), int(height))
            sx, sy = to_stream(x, y, image_size, stream)
            for message in (
                new_method_call(
                    remote,
                    "NotifyPointerMotionAbsolute",
                    "oa{sv}udd",
                    (session, {}, stream.node, sx, sy),
                ),
                new_method_call(
                    remote, "NotifyPointerButton", "oa{sv}iu", (session, {}, BTN_LEFT, 1)
                ),
                new_method_call(
                    remote, "NotifyPointerButton", "oa{sv}iu", (session, {}, BTN_LEFT, 0)
                ),
            ):
                await _send(router, message)
            log.info("portal_clicked")
        finally:
            closing = DBusAddress(session, PORTAL, "org.freedesktop.portal.Session")
            try:
                await _send(router, new_method_call(closing, "Close"))
            except Exception:
                log.warning("portal_session_not_closed")


async def _send(router: Any, message: Any) -> Any:
    """Un appel D-Bus, borné dans le temps (le routeur de jeepney n'a pas de délai)."""
    import asyncio  # noqa: PLC0415

    return await asyncio.wait_for(router.send_and_get_reply(message), REQUEST_TIMEOUT_S)


class _Requests:
    """Une méthode du portail rend un objet « Request » ; sa réponse arrive par un signal,
    auquel on s'abonne avant de poser la question."""

    def __init__(self, router: Any) -> None:
        self._router = router
        sender = (router.unique_name or "").lstrip(":").replace(".", "_")
        self._prefix = f"{PORTAL_PATH}/request/{sender}/"

    async def call(
        self,
        build: Callable[[str], Any],
        *,
        wait_s: float = REQUEST_TIMEOUT_S,
        refused: str = "GNOME a refusé la demande : clic annulé.",
    ) -> dict[str, Any]:
        import asyncio  # noqa: PLC0415

        from jeepney import MatchRule  # noqa: PLC0415
        from jeepney.bus_messages import message_bus  # noqa: PLC0415

        handle = "ojarvis" + secrets.token_hex(6)
        rule = MatchRule(
            type="signal",
            interface="org.freedesktop.portal.Request",
            member="Response",
            path=self._prefix + handle,
        )
        await _send(self._router, message_bus.AddMatch(rule))
        with self._router.filter(rule) as responses:
            await _send(self._router, build(handle))
            try:
                signal = await asyncio.wait_for(responses.get(), wait_s)
            except TimeoutError as exc:
                raise UnavailableError(
                    "GNOME n'a pas répondu à temps (fenêtre d'autorisation restée ouverte ?) : "
                    "rien n'a été cliqué."
                ) from exc
        code, results = signal.body
        if code != 0:
            raise UnavailableError(refused)
        return dict(results)
