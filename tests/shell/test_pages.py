"""Voir une page servie sur ta machine : adresses permises, serveurs reconnus, vraie capture."""

from __future__ import annotations

import asyncio
import contextlib
import io
import os
import signal
import socket
from pathlib import Path

import httpx
import pytest
from PIL import Image

from ojarvis.core.errors import UpstreamError
from ojarvis.llm.catalog import Catalog
from ojarvis.llm.providers.fake import FakeProvider, FakeReply
from ojarvis.llm.router import ModelRouter
from ojarvis.llm.types import LLMRequest
from ojarvis.shell.pages import PageShot, blocked_request, capture_page, local_page
from ojarvis.shell.servers import BackgroundSessions, session_of
from ojarvis.tools.base import Level, ToolContext
from ojarvis.tools.guards import BlockedTargetError
from ojarvis.tools.page_view import LookAtPage
from ojarvis.tools.shell import start_background

CHROMIUM = list(Path.home().glob(".cache/ms-playwright/chromium-*"))


def test_only_pages_served_on_the_machine() -> None:
    assert local_page("http://localhost:5173/") == ("http://localhost:5173/", 5173)
    assert local_page("http://127.0.0.1:8000/a?b=1") == ("http://127.0.0.1:8000/a?b=1", 8000)
    for refused in ("https://example.com", "file:///etc/passwd", "http://192.168.1.1/", "ftp://x"):
        with pytest.raises(BlockedTargetError):
            local_page(refused)


@pytest.mark.parametrize(
    ("url", "blocked"),
    [
        ("http://localhost:5173/src/main.js", False),
        ("https://fonts.googleapis.com/css", False),
        ("http://192.168.1.1/admin", True),
        ("http://10.0.0.5/", True),
        ("http://imprimante.local/", True),
        ("http://169.254.169.254/latest/meta-data", True),
    ],
)
def test_the_page_cannot_reach_the_local_network(url: str, blocked: bool) -> None:
    assert blocked_request(url) is blocked


def test_the_session_is_read_from_proc(tmp_path: Path) -> None:
    (tmp_path / "123").mkdir()
    # Le nom peut contenir des espaces et des parenthèses : on lit après la dernière.
    (tmp_path / "123" / "stat").write_text("123 (node (vite)) S 1 4242 4242 0 -1 4194560")
    assert session_of(123, proc=tmp_path) == 4242
    assert session_of(999, proc=tmp_path) is None


def test_a_port_belongs_to_a_server_started_in_shell_mode() -> None:
    sessions = BackgroundSessions(
        pids_on_port=lambda port: {10, 11} if port == 5173 else {20},
        session={10: 7, 11: 7, 20: 99}.get,
    )
    assert not sessions.owns_port(5173)
    sessions.add(7)
    assert sessions.owns_port(5173)
    assert not sessions.owns_port(8080)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def test_a_real_background_server_is_recognised(tmp_path: Path) -> None:
    port = free_port()
    started = await start_background(
        f"python3 -m http.server {port} --bind 127.0.0.1",
        tmp_path,
        env=dict(os.environ),
        log_dir=tmp_path / "journaux",
    )
    try:
        sessions = BackgroundSessions()
        assert not sessions.owns_port(port)
        sessions.add(started.pid)
        for _ in range(50):  # le serveur met un instant à écouter
            if sessions.owns_port(port):
                break
            await asyncio.sleep(0.1)
        assert sessions.owns_port(port)
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(started.pid, signal.SIGKILL)


# --------------------------------------------------------------------------- l'outil


def tool(
    home: Path, owned: bool, mode: str = "en_ligne", reply: str = "Un titre, 3 cartes."
) -> LookAtPage:
    def vision(request: LLMRequest) -> FakeReply:
        assert request.messages[-1].images  # la capture part avec la question
        return FakeReply(text=reply)

    router = ModelRouter({"deepseek": FakeProvider("deepseek", script=vision)}, Catalog())
    sessions = BackgroundSessions(pids_on_port=lambda _p: {1}, session=lambda _p: 1)
    if owned:
        sessions.add(1)

    async def shoot(url: str) -> PageShot:
        del url
        return PageShot(PNG, "Ma page", 200, ["Uncaught TypeError: x is undefined"])

    return LookAtPage(
        router,
        lambda: mode,  # type: ignore[arg-type,return-value]
        sessions,
        capture_dir=home / "captures",
        shoot=shoot,
    )


def png() -> bytes:
    """Une vraie petite image PNG, pour la réduction d'image."""
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(buffer, "PNG")
    return buffer.getvalue()


PNG = png()


def context(home: Path) -> ToolContext:
    return ToolContext(http=httpx.AsyncClient(), secret=lambda _name: None, home=home)


def test_a_page_of_a_server_jarvis_started_is_a_read(tmp_path: Path) -> None:
    owned, foreign = tool(tmp_path, owned=True), tool(tmp_path, owned=False)
    args = owned.parse({"adresse": "http://localhost:5173/"})
    assert owned.level_for(args) == Level.LIRE
    assert foreign.level_for(args) == Level.AGIR  # un autre port : ta validation
    assert owned.shell_only
    assert (
        owned.describe(args)
        == "Regarder la page http://localhost:5173/ (capture lue par un modèle en ligne)"
    )


async def test_the_page_is_described_with_its_status_errors_and_capture(tmp_path: Path) -> None:
    look = tool(tmp_path, owned=True)
    output = await look.run(look.parse({"adresse": "http://localhost:5173/"}), context(tmp_path))
    assert output.external  # ce que montre la page est une donnée
    assert "statut HTTP 200, titre « Ma page »" in output.text
    assert "- Uncaught TypeError: x is undefined" in output.text
    assert "--- vu par deepseek / deepseek-v4-flash-vision-exp ---" in output.text
    assert output.text.endswith("Un titre, 3 cartes.")
    capture = next((tmp_path / "captures").glob("*.png"))
    assert capture.stat().st_mode & 0o777 == 0o600


async def test_with_local_reading_and_no_local_model_the_hint_is_given(tmp_path: Path) -> None:
    look = tool(tmp_path, owned=True, mode="local")
    with pytest.raises(UpstreamError, match="Lecture de l'écran : En ligne"):
        await look.run(look.parse({"adresse": "http://localhost:5173/"}), context(tmp_path))


@pytest.mark.skipif(not CHROMIUM, reason="Chromium de Playwright absent")
async def test_a_real_page_is_captured_with_its_console_errors(tmp_path: Path) -> None:
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text(
        "<!doctype html><title>Essai Jarvis</title><h1>Bonjour</h1>"
        "<script>console.error('oups depuis la page')</script>"
    )
    port = free_port()
    started = await start_background(
        f"python3 -m http.server {port} --bind 127.0.0.1",
        site,
        env=dict(os.environ),
        log_dir=tmp_path / "journaux",
    )
    try:
        async with httpx.AsyncClient() as http:
            for _ in range(50):
                with contextlib.suppress(httpx.HTTPError):
                    await http.get(f"http://127.0.0.1:{port}/", timeout=1)
                    break
                await asyncio.sleep(0.1)
        shot = await capture_page(f"http://127.0.0.1:{port}/")
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(started.pid, signal.SIGKILL)
    assert shot.status == 200
    assert shot.title == "Essai Jarvis"
    assert shot.png.startswith(b"\x89PNG")
    assert shot.errors == ["oups depuis la page"]


@pytest.mark.skipif(not CHROMIUM, reason="Chromium de Playwright absent")
async def test_a_page_that_does_not_answer_says_so() -> None:
    with pytest.raises(UpstreamError, match="Le serveur tourne-t-il"):
        await capture_page(f"http://127.0.0.1:{free_port()}/", timeout_s=5)
