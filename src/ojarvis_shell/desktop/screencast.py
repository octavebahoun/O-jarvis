"""Partager l'écran en direct avec Jarvis : le portail ScreenCast de GNOME, lu par GStreamer.

Sans partage, chaque regard sur l'écran est une capture (portail Screenshot). Partagé, l'écran
arrive en flux : deux images par seconde, dont seule la dernière est gardée, en mémoire. Les
outils d'écran (regarder, trouver un élément, vérifier avant un clic) la lisent tout de suite,
et l'app la montre en aperçu.

- GNOME demande ton accord la première fois (puis il est retenu, jeton lisible par toi seul)
  et affiche son indicateur de partage tant que dure le partage ; tu l'arrêtes depuis l'app
  ou depuis cet indicateur.
- Rien n'est enregistré sur le disque, rien ne part ailleurs : une image ne quitte la machine
  que quand un outil l'envoie à un modèle, comme une capture.
- Le curseur n'est pas dans les images : il ne fausse pas la comparaison faite avant un clic.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import secrets
import shutil
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anyio.to_thread

from ojarvis.core.errors import UnavailableError
from ojarvis.core.logs import get_logger

log = get_logger(__name__)

PORTAL = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
MONITOR = 1
CURSOR_HIDDEN = 1
PERSIST_UNTIL_REVOKED = 2
ANSWER_TIMEOUT_S = 90.0
REQUEST_TIMEOUT_S = 15.0
FPS = 2
#: Une image plus vieille que cela n'est plus « en direct » : on refait une capture.
FRESH_S = 3.0
NOTIFY_EVERY_S = 0.5
JPEG_END = b"\xff\xd9"

Notify = Callable[[str, dict[str, Any]], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class Session:
    connection: Any
    handle: str
    node: int
    width: int
    height: int
    fd: int


def _variant(results: dict[str, Any], key: str, default: Any = None) -> Any:
    value = results.get(key)
    return value[1] if isinstance(value, tuple) and len(value) == 2 else default


def _request(connection: Any, build: Callable[[str], Any], *, wait_s: float, refused: str) -> Any:
    """Une méthode du portail, puis sa réponse (un signal), à laquelle on s'abonne avant."""
    from jeepney import MatchRule  # noqa: PLC0415
    from jeepney.bus_messages import message_bus  # noqa: PLC0415

    handle = "ojarvis" + secrets.token_hex(6)
    sender = (connection.unique_name or "").lstrip(":").replace(".", "_")
    rule = MatchRule(
        type="signal",
        interface="org.freedesktop.portal.Request",
        member="Response",
        path=f"{PORTAL_PATH}/request/{sender}/{handle}",
    )
    connection.send_and_get_reply(message_bus.AddMatch(rule), timeout=REQUEST_TIMEOUT_S)
    with connection.filter(rule) as responses:
        connection.send_and_get_reply(build(handle), timeout=REQUEST_TIMEOUT_S)
        try:
            signal = connection.recv_until_filtered(responses, timeout=wait_s)
        except TimeoutError as exc:
            raise UnavailableError(
                "GNOME n'a pas répondu à temps (fenêtre de partage restée ouverte ?) : rien "
                "n'est partagé."
            ) from exc
    code, results = signal.body
    if code != 0:
        raise UnavailableError(refused)
    return results


def open_session(token_file: Path) -> Session:
    """Demande le partage de l'écran au portail (bloquant : la fenêtre de GNOME attend toi)."""
    from jeepney import DBusAddress, new_method_call  # noqa: PLC0415
    from jeepney.io.blocking import open_dbus_connection  # noqa: PLC0415

    connection = open_dbus_connection(bus="SESSION", enable_fds=True)
    portal = DBusAddress(PORTAL_PATH, PORTAL, "org.freedesktop.portal.ScreenCast")
    try:
        created = _request(
            connection,
            lambda h: new_method_call(
                portal,
                "CreateSession",
                "a{sv}",
                ({"handle_token": ("s", h), "session_handle_token": ("s", h)},),
            ),
            wait_s=REQUEST_TIMEOUT_S,
            refused="GNOME n'a pas ouvert de session de partage.",
        )
        handle = str(_variant(created, "session_handle", ""))
        options: dict[str, Any] = {
            "types": ("u", MONITOR),
            "multiple": ("b", False),
            "cursor_mode": ("u", CURSOR_HIDDEN),
            "persist_mode": ("u", PERSIST_UNTIL_REVOKED),
        }
        try:
            token = token_file.read_text(encoding="utf-8").strip()
        except OSError:
            token = ""
        if token:
            options["restore_token"] = ("s", token)
        _request(
            connection,
            lambda h: new_method_call(
                portal, "SelectSources", "oa{sv}", (handle, {**options, "handle_token": ("s", h)})
            ),
            wait_s=REQUEST_TIMEOUT_S,
            refused="GNOME a refusé de choisir un écran à partager.",
        )
        started = _request(
            connection,
            lambda h: new_method_call(
                portal, "Start", "osa{sv}", (handle, "", {"handle_token": ("s", h)})
            ),
            wait_s=ANSWER_TIMEOUT_S,
            refused="Partage refusé : GNOME n'a pas reçu ton accord. Rien n'est partagé.",
        )
        new_token = _variant(started, "restore_token")
        if new_token:
            token_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            token_file.write_text(str(new_token), encoding="utf-8")
            token_file.chmod(0o600)
        streams = _variant(started, "streams", []) or []
        if not streams:
            raise UnavailableError("GNOME n'a partagé aucun écran.")
        node, properties = streams[0]
        width, height = _variant(properties, "size", (0, 0))
        reply = connection.send_and_get_reply(
            new_method_call(portal, "OpenPipeWireRemote", "oa{sv}", (handle, {})),
            timeout=REQUEST_TIMEOUT_S,
        )
        fd = reply.body[0].to_raw_fd()
        return Session(connection, handle, int(node), int(width), int(height), fd)
    except Exception:
        connection.close()
        raise


def close_session(session: Session) -> None:
    from jeepney import DBusAddress, new_method_call  # noqa: PLC0415

    with contextlib.suppress(OSError):
        os.close(session.fd)  # GStreamer garde sa propre copie tant qu'il tourne
    with contextlib.suppress(Exception):
        closing = DBusAddress(session.handle, PORTAL, "org.freedesktop.portal.Session")
        session.connection.send_and_get_reply(
            new_method_call(closing, "Close"), timeout=REQUEST_TIMEOUT_S
        )
    with contextlib.suppress(Exception):
        session.connection.close()


def pipeline(session: Session) -> list[str]:
    """GStreamer : le flux PipeWire, deux images par seconde, en JPEG à la suite sur stdout."""
    gst = shutil.which("gst-launch-1.0")
    if gst is None:
        raise UnavailableError(
            "GStreamer manque (gst-launch-1.0) : installe gstreamer1.0-tools et "
            "gstreamer1.0-pipewire."
        )
    return [
        gst,
        "-q",
        "pipewiresrc",
        f"fd={session.fd}",
        f"path={session.node}",
        "do-timestamp=true",
        "keepalive-time=1000",
        "!",
        "videoconvert",
        "!",
        "videorate",
        "!",
        f"video/x-raw,framerate={FPS}/1",
        "!",
        "jpegenc",
        "quality=85",
        "!",
        "fdsink",
        "fd=1",
    ]


def split_frames(buffer: bytes) -> tuple[list[bytes], bytes]:
    """Les images JPEG complètes du tampon, et le reste (une image pas encore finie)."""
    frames: list[bytes] = []
    while True:
        end = buffer.find(JPEG_END)
        if end < 0:
            return frames, buffer
        frame, buffer = buffer[: end + 2], buffer[end + 2 :]
        start = frame.find(b"\xff\xd8")
        if start >= 0:
            frames.append(frame[start:])


Opener = Callable[[Path], Session]
Closer = Callable[[Session], None]


class ScreenShare:
    def __init__(
        self,
        token_file: Path,
        *,
        notify: Notify | None = None,
        opener: Opener = open_session,
        closer: Closer = close_session,
        command: Callable[[Session], list[str]] = pipeline,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._token_file = token_file
        self._notify = notify
        self._open = opener
        self._close = closer
        self._command = command
        self._clock = clock
        self._session: Session | None = None
        self._process: asyncio.subprocess.Process | None = None
        self._reader: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._frame: tuple[bytes, float] | None = None
        self.seq = 0
        self.since: float | None = None
        self._last_notice = float("-inf")

    @property
    def active(self) -> bool:
        return self._process is not None and self._process.returncode is None

    def state(self) -> dict[str, Any]:
        return {"active": self.active, "frames": self.seq, "since": self.since}

    def frame(self) -> bytes | None:
        """La dernière image, si elle est assez récente pour être « en direct »."""
        if self._frame is None or not self.active:
            return None
        image, at = self._frame
        return image if self._clock() - at <= FRESH_S else None

    async def start(self) -> None:
        async with self._lock:
            if self.active:
                return
            session = await anyio.to_thread.run_sync(self._open, self._token_file)
            try:
                self._process = await asyncio.create_subprocess_exec(
                    *self._command(session),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                    stdin=asyncio.subprocess.DEVNULL,
                    pass_fds=(session.fd,),
                )
            except Exception:
                await anyio.to_thread.run_sync(self._close, session)
                raise
            self._session = session
            self.seq = 0
            self._frame = None
            self.since = time.time()
            self._reader = asyncio.create_task(self._read(self._process))
            log.info("screen_share_started")
        await self._publish("screen.share", self.state())

    async def _read(self, process: asyncio.subprocess.Process) -> None:
        stdout = process.stdout
        buffer = b""
        try:
            while stdout is not None and (chunk := await stdout.read(65536)):
                frames, buffer = split_frames(buffer + chunk)
                if frames:
                    self._frame = (frames[-1], self._clock())
                    self.seq += len(frames)
                    if self._clock() - self._last_notice >= NOTIFY_EVERY_S:
                        self._last_notice = self._clock()
                        await self._publish("screen.frame", {"seq": self.seq})
        finally:
            # Fin du flux : tu as arrêté le partage depuis GNOME, ou GStreamer s'est arrêté.
            if self._process is process and process.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
            await process.wait()
            if self._process is process:
                await self._end()

    async def stop(self) -> None:
        async with self._lock:
            process = self._process
            if process is None:
                return
            with contextlib.suppress(ProcessLookupError):
                process.terminate()
            reader = self._reader
        if reader is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(reader, 5)

    async def _end(self) -> None:
        session, self._session = self._session, None
        self._process = None
        self._reader = None
        self._frame = None
        self.since = None
        if session is not None:
            await anyio.to_thread.run_sync(self._close, session)
        log.info("screen_share_stopped")
        await self._publish("screen.share", self.state())

    async def _publish(self, event: str, payload: dict[str, Any]) -> None:
        if self._notify is not None:
            await self._notify(event, payload)


def live_or_capture(
    share: ScreenShare, capture: Callable[[], Awaitable[bytes]]
) -> Callable[[], Awaitable[bytes]]:
    """L'écran tel qu'il est : l'image en direct si le partage tourne, sinon une capture."""

    async def grab() -> bytes:
        frame = share.frame()
        return frame if frame is not None else await capture()

    return grab
