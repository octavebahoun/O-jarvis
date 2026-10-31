"""Filmer l'écran sur demande (#50) : un extrait, une vidéo pour Muse Spark, rien de gardé."""

from __future__ import annotations

import base64
import io
import json
import shutil
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from ojarvis.core.errors import InvalidInputError, UnavailableError
from ojarvis.desktop.film import MODEL, Filmer, answer_text
from ojarvis.tools.base import Level, ToolContext
from ojarvis.tools.screen import FilmArgs, FilmScreen


def jpeg(color: str) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (320, 180), color).save(out, "JPEG")
    return out.getvalue()


class FakeShare:
    def __init__(self, frames: list[bytes], *, active: bool = False) -> None:
        self.frames = frames
        self.active = active
        self.calls: list[str] = []

    async def start(self) -> None:
        self.calls.append("start")
        self.active = True

    async def stop(self) -> None:
        self.calls.append("stop")
        self.active = False

    async def record(self, seconds: float) -> list[bytes]:
        self.calls.append(f"record {seconds:g}")
        return self.frames


def muse(answer: dict[str, Any], seen: list[dict[str, Any]]) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=answer)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_the_answer_text_is_read_in_both_shapes() -> None:
    assert answer_text({"output_text": " Un menu s'ouvre. "}) == "Un menu s'ouvre."
    assert (
        answer_text(
            {
                "output": [
                    {"type": "reasoning", "summary": []},
                    {"type": "message", "content": [{"type": "output_text", "text": "Vers 3 s"}]},
                ]
            }
        )
        == "Vers 3 s"
    )


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg absent")
async def test_a_clip_is_filmed_sent_to_muse_spark_and_not_kept(tmp_path: Path) -> None:
    share = FakeShare([jpeg("white"), jpeg("red"), jpeg("white")])
    seen: list[dict[str, Any]] = []
    work = tmp_path / "travail"
    async with muse(
        {"output_text": "Vers 1 s, un bandeau rouge apparaît.", "usage": {}}, seen
    ) as http:
        filmer = Filmer(share, lambda: "cle", http, work)  # type: ignore[arg-type]
        answer = await filmer.film("Qu'est-ce qui clignote ?", 99)
    assert answer == "Vers 1 s, un bandeau rouge apparaît."
    # Le partage ne tournait pas : il est lancé pour l'extrait (30 s au plus), puis arrêté.
    assert share.calls == ["start", "record 30", "stop"]
    body = seen[0]
    assert body["model"] == MODEL
    text, video = body["input"][0]["content"]
    assert "Qu'est-ce qui clignote ?" in text["text"]
    assert video["type"] == "input_video"
    assert video["video_url"].startswith("data:video/mp4;base64,")
    mp4 = base64.b64decode(video["video_url"].split(",", 1)[1])
    assert mp4[4:8] == b"ftyp"  # une vraie vidéo MP4
    assert list(work.iterdir()) == []  # rien n'est gardé sur le disque


async def test_a_running_share_is_left_running_and_no_image_means_no_film(
    tmp_path: Path,
) -> None:
    share = FakeShare([jpeg("white")], active=True)
    async with muse({"output_text": "?"}, []) as http:
        filmer = Filmer(share, lambda: "cle", http, tmp_path)  # type: ignore[arg-type]
        with pytest.raises(UnavailableError, match="aucune image"):
            await filmer.film("Que se passe-t-il ?", 5)
    assert share.calls == ["record 5"]  # le partage que tu avais lancé continue


async def test_the_tool_refuses_when_the_screen_must_stay_local(tmp_path: Path) -> None:
    share = FakeShare([jpeg("white"), jpeg("red")])
    async with muse({"output_text": "Un menu s'ouvre puis se ferme."}, []) as http:
        filmer = Filmer(share, lambda: "cle", http, tmp_path)  # type: ignore[arg-type]
        context = ToolContext(http=None, secret=lambda _n: None, home=tmp_path)  # type: ignore[arg-type]
        local = FilmScreen(filmer, lambda: "local")
        with pytest.raises(InvalidInputError, match="sur ce PC"):
            await local.run(FilmArgs(question="Pourquoi ce menu se ferme ?"), context)
        assert share.calls == []  # rien n'est filmé
        tool = FilmScreen(filmer, lambda: "en_ligne")
        assert tool.level == Level.LIRE
        args = FilmArgs(question="Pourquoi ce menu se ferme ?", duree_s=10)
        assert tool.describe(args) == (
            "Filmer ton écran pendant 10 s : « Pourquoi ce menu se ferme ? »"
        )
        if shutil.which("ffmpeg") is None:
            return
        output = await tool.run(args, context)
    assert output.text == "Un menu s'ouvre puis se ferme."
    assert output.external  # ce que montre l'écran est une donnée


def test_filming_needs_pc_control(client: TestClient) -> None:
    tools = {tool["name"]: tool for tool in client.get("/v1/tools").json()}
    assert tools["filmer_ecran"]["level"] == 1
    assert tools["filmer_ecran"]["os_control"]
