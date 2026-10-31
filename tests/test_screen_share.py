"""L'écran partagé en direct : le flux lu image par image, les outils qui s'en servent, et
l'arrêt, depuis l'app ou depuis GNOME."""

from __future__ import annotations

import asyncio
import contextlib
import io
import os
import shutil
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from ojarvis.desktop.screencast import ScreenShare, Session, live_or_capture, split_frames
from ojarvis.services import Services


def jpeg(color: str) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (64, 36), color).save(out, "JPEG")
    return out.getvalue()


@pytest.fixture
def pipe_fd() -> Iterator[int]:
    """Un descripteur à passer à GStreamer, comme celui de PipeWire."""
    read, write = os.pipe()
    yield read
    for fd in (read, write):
        with contextlib.suppress(OSError):
            os.close(fd)


def frames_then(frames: list[bytes], *, then: str) -> list[str]:
    """Un faux GStreamer : des images JPEG à la suite sur stdout, puis attendre ou finir."""
    script = (
        "import sys, time\n"
        f"for frame in {frames!r}:\n"
        "    sys.stdout.buffer.write(frame); sys.stdout.buffer.flush(); time.sleep(0.02)\n"
        f"{'time.sleep(60)' if then == 'wait' else 'pass'}\n"
    )
    return [sys.executable, "-c", script]


class Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.closed: list[Session] = []

    async def notify(self, event: str, payload: dict[str, Any]) -> None:
        self.events.append((event, payload))

    def close(self, session: Session) -> None:
        self.closed.append(session)


async def wait_until(condition: Any, timeout_s: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_s
    while not condition():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("délai dépassé")
        await asyncio.sleep(0.01)


def test_the_stream_is_cut_into_whole_jpeg_images() -> None:
    red, blue = jpeg("red"), jpeg("blue")
    frames, rest = split_frames(red + blue[:10])
    assert frames == [red]
    assert rest == blue[:10]
    frames, rest = split_frames(rest + blue[10:])
    assert frames == [blue]
    assert rest == b""


async def test_the_last_image_is_kept_and_the_share_stops_cleanly(
    tmp_path: Path, pipe_fd: int
) -> None:
    recorder = Recorder()
    session = Session(None, "/session/1", 42, 1366, 768, pipe_fd)
    images = [jpeg("red"), jpeg("green"), jpeg("blue")]
    share = ScreenShare(
        tmp_path / "jeton",
        notify=recorder.notify,
        opener=lambda _token: session,
        closer=recorder.close,
        command=lambda _session: frames_then(images, then="wait"),
    )
    await share.start()
    assert share.active
    await wait_until(lambda: share.seq == 3)
    assert share.frame() == images[-1]
    first, payload = recorder.events[0]
    assert (first, payload["active"]) == ("screen.share", True)  # l'app affiche le bandeau
    assert any(event == "screen.frame" for event, _ in recorder.events)
    await share.stop()
    assert share.state()["active"] is False
    assert share.frame() is None  # plus rien n'est gardé
    assert recorder.closed == [session]  # la session du portail est fermée
    assert recorder.events[-1] == ("screen.share", {"active": False, "frames": 3, "since": None})


async def test_stopping_from_gnome_ends_the_share(tmp_path: Path, pipe_fd: int) -> None:
    # L'indicateur de GNOME arrête le partage : le flux s'interrompt, Jarvis le voit.
    recorder = Recorder()
    session = Session(None, "/session/2", 42, 1366, 768, pipe_fd)
    share = ScreenShare(
        tmp_path / "jeton",
        notify=recorder.notify,
        opener=lambda _token: session,
        closer=recorder.close,
        command=lambda _session: frames_then([jpeg("red")], then="end"),
    )
    await share.start()
    await wait_until(lambda: not share.active and recorder.closed)
    assert recorder.events[-1][1]["active"] is False


async def test_an_old_image_is_not_live_and_tools_take_a_capture_then(
    tmp_path: Path, pipe_fd: int
) -> None:
    now = {"t": 100.0}
    session = Session(None, "/session/3", 42, 1366, 768, pipe_fd)
    share = ScreenShare(
        tmp_path / "jeton",
        opener=lambda _token: session,
        closer=lambda _session: None,
        command=lambda _session: frames_then([jpeg("red")], then="wait"),
        clock=lambda: now["t"],
    )
    captured: list[str] = []

    async def capture() -> bytes:
        captured.append("capture")
        return b"capture"

    grab = live_or_capture(share, capture)
    assert await grab() == b"capture"  # pas de partage : une capture
    await share.start()
    await wait_until(lambda: share.seq == 1)
    assert await grab() == jpeg("red")  # l'image en direct, sans capture
    now["t"] += 10  # le flux s'est figé
    assert await grab() == b"capture"
    assert captured == ["capture", "capture"]
    await share.stop()


@pytest.mark.skipif(shutil.which("gst-launch-1.0") is None, reason="GStreamer absent")
async def test_real_gstreamer_jpeg_images_are_read(tmp_path: Path, pipe_fd: int) -> None:
    # Le même encodage que pour l'écran, sur une mire : les images arrivent entières.
    session = Session(None, "/session/4", 42, 320, 180, pipe_fd)
    command = [
        str(shutil.which("gst-launch-1.0")),
        "-q",
        "videotestsrc",
        "!",
        "video/x-raw,width=320,height=180,framerate=10/1",
        "!",
        "jpegenc",
        "!",
        "fdsink",
        "fd=1",
    ]
    share = ScreenShare(
        tmp_path / "jeton",
        opener=lambda _token: session,
        closer=lambda _session: None,
        command=lambda _session: command,
    )
    await share.start()
    await wait_until(lambda: share.seq >= 3)
    frame = share.frame()
    assert frame is not None
    with Image.open(io.BytesIO(frame)) as image:
        assert image.size == (320, 180)
    await share.stop()


def test_sharing_needs_pc_control_and_the_app_reads_the_frame(
    client: TestClient, services: Services
) -> None:
    assert client.get("/v1/desktop/share").json() == {"active": False, "frames": 0, "since": None}
    refused = client.post("/v1/desktop/share/start")
    assert refused.status_code == 409
    assert "Contrôle du PC" in refused.json()["error"]["message"]
    assert client.get("/v1/desktop/share/frame").status_code == 404
    assert client.post("/v1/desktop/share/stop").json()["active"] is False
    del services
