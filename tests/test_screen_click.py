"""Cliquer dans une application de bureau (#48) : SAM 3.1 encadre, Muse Spark choisit,
le portail de GNOME clique, et rien n'est cliqué au hasard."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from ojarvis.core.errors import InvalidInputError, NotFoundError, UnavailableError
from ojarvis.desktop.locate import AmbiguousTargetError, Locator, ScreenMarks, unchanged
from ojarvis.desktop.pointer import Stream, to_stream
from ojarvis.desktop.sam import Box, Segmentation, parse_boxes
from ojarvis.llm.catalog import Catalog
from ojarvis.llm.providers.fake import FakeProvider, FakeReply
from ojarvis.llm.router import ModelRouter
from ojarvis.llm.structured import TOOL_NAME
from ojarvis.llm.types import LLMRequest, ToolCall
from ojarvis.services import Services
from ojarvis.tools.base import Level, ToolContext
from ojarvis.tools.desktop import ClickArgs, ClickOnScreen, FindArgs, FindOnScreen

#: Deux boutons gris sur une fenêtre blanche de 400 × 200.
BUTTONS = (Box(0, 20, 20, 119, 59), Box(1, 250, 140, 369, 179))


def window(*, covered: bool = False) -> bytes:
    image = Image.new("RGB", (400, 200), "white")
    draw = ImageDraw.Draw(image)
    for box in BUTTONS:
        draw.rectangle((box.x1, box.y1, box.x2, box.y2), fill=(200, 200, 200), outline="black")
    if covered:  # une fenêtre passée devant le second bouton
        draw.rectangle((230, 120, 399, 199), fill=(40, 60, 90))
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


class FakeSam:
    def __init__(self, found: dict[str, tuple[Box, ...]]) -> None:
        self.found = found
        self.concepts: list[str] = []

    async def find(self, image: bytes, concept: str, *, mime: str = "image/png") -> Segmentation:
        del image, mime
        self.concepts.append(concept)
        return Segmentation(self.found.get(concept, ()), 400, 200)


def muse(*picks: dict[str, Any]) -> tuple[ModelRouter, FakeProvider]:
    """Muse Spark (profil « Tout Meta ») qui répond, dans l'ordre, ces choix."""
    answers = list(picks)

    def script(request: LLMRequest) -> FakeReply:
        assert request.messages[-2].images  # il voit la capture numérotée
        return FakeReply(tool_calls=[ToolCall("p", TOOL_NAME, answers.pop(0))])

    provider = FakeProvider("meta", script=script)
    router = ModelRouter({"meta": provider}, Catalog(), profile=lambda: "meta")
    return router, provider


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def locator(
    tmp_path: Path, sam: FakeSam, *picks: dict[str, Any], clock: Clock | None = None
) -> Locator:
    router, _ = muse(*picks)
    marks = ScreenMarks(tmp_path / "reperes", clock=clock or Clock())

    async def grab() -> bytes:
        return window()

    return Locator(sam, router, marks, grab)  # type: ignore[arg-type]


def test_sam_boxes_are_read_with_or_without_confidence() -> None:
    text = (
        "<0f>0<|box;x1=96;y1=35;x2=218;y2=61;w=1280;h=720;c=0.76|><|mask;x=0;y=0;c=0.76;"
        "data=27,123,~!!M0c|>,2<|box;x1=1152;y1=655;x2=1249;y2=689;w=1280;h=720|>"
        "<|mask;x=0;y=0;data=35,98,!x~y|>"
    )
    boxes, size = parse_boxes(text)
    assert boxes == [Box(0, 96, 35, 218, 61, 0.76), Box(2, 1152, 655, 1249, 689, None)]
    assert size == (1280, 720)
    assert boxes[0].center == (157, 48)
    assert parse_boxes("") == ([], None)  # rien trouvé : pas une erreur


def test_a_point_of_the_capture_lands_on_the_same_point_of_the_screen() -> None:
    assert to_stream(157, 48, (1366, 768), Stream(42, 1366, 768)) == (157, 48)
    # Écran mis à l'échelle (200 %) : la capture a deux fois plus de pixels.
    assert to_stream(300, 100, (2732, 1536), Stream(42, 1366, 768)) == (150, 50)
    with pytest.raises(UnavailableError, match="plusieurs écrans"):
        to_stream(10, 10, (3286, 1080), Stream(42, 1366, 768))


async def test_sam_frames_and_muse_spark_picks_the_element(tmp_path: Path) -> None:
    sam = FakeSam({"button": BUTTONS})
    found = await locator(
        tmp_path, sam, {"numero": 2, "candidats": [], "vu": "Bouton Enregistrer en bas"}
    ).find("le bouton Enregistrer", "bouton")
    assert found.box == BUTTONS[1]
    assert found.center == (309, 159)
    assert (found.width, found.height) == (400, 200)
    assert found.seen == "Bouton Enregistrer en bas"
    assert found.image.is_file()
    assert found.image.stat().st_mode & 0o777 == 0o600  # une capture de ton écran
    assert sam.concepts == ["button"]


async def test_nothing_found_asks_instead_of_guessing(tmp_path: Path) -> None:
    # SAM ne voit aucun bouton, puis Muse Spark ne reconnaît aucune icône (« null » en texte).
    sam = FakeSam({"icon": BUTTONS})
    finder = locator(tmp_path, sam, {"numero": "null", "candidats": []})
    with pytest.raises(AmbiguousTargetError, match="Je ne trouve pas « le bouton Supprimer »"):
        await finder.find("le bouton Supprimer", "bouton")
    assert sam.concepts == ["button", "icon"]


async def test_two_possible_elements_ask_which_one(tmp_path: Path) -> None:
    sam = FakeSam({"button": BUTTONS})
    both = {
        "numero": 1,
        "candidats": [{"numero": 1, "ou": "en haut à gauche"}, {"numero": 2, "ou": "en bas"}],
    }
    with pytest.raises(AmbiguousTargetError, match=r"n° 1, en haut à gauche ; n° 2, en bas"):
        await locator(tmp_path, sam, both).find("le bouton Exporter", "bouton")


def test_the_element_must_still_be_there_to_be_clicked(tmp_path: Path) -> None:
    marks = ScreenMarks(tmp_path)
    mark = marks.keep("le bouton Enregistrer", BUTTONS[1], window(), (400, 200), "")
    assert unchanged(mark, window())
    assert not unchanged(mark, window(covered=True))  # une fenêtre est passée devant
    small = io.BytesIO()
    Image.new("RGB", (200, 100), "white").save(small, "PNG")
    assert not unchanged(mark, small.getvalue())  # l'écran a changé de taille


class FakePointer:
    def __init__(self) -> None:
        self.clicks: list[tuple[int, int, tuple[int, int]]] = []

    async def click(self, x: int, y: int, image_size: tuple[int, int]) -> None:
        self.clicks.append((x, y, image_size))


def context(home: Path) -> ToolContext:
    return ToolContext(http=None, secret=lambda _name: None, home=home)  # type: ignore[arg-type]


async def test_a_click_only_happens_on_a_recent_unchanged_mark(tmp_path: Path) -> None:
    clock = Clock()
    marks = ScreenMarks(tmp_path / "reperes", clock=clock)
    mark = marks.keep("le bouton Enregistrer", BUTTONS[1], window(), (400, 200), "")
    screen = {"image": window(covered=True)}

    async def grab() -> bytes:
        return screen["image"]

    pointer = FakePointer()
    tool = ClickOnScreen(marks, pointer, grab)
    assert tool.level == Level.AGIR
    assert tool.describe(ClickArgs(repere=mark.id)) == (
        f"Cliquer sur le bouton Enregistrer (repère {mark.id})"
    )
    with pytest.raises(InvalidInputError, match="rien n'a été cliqué"):
        await tool.run(ClickArgs(repere=mark.id), context(tmp_path))
    assert pointer.clicks == []
    screen["image"] = window()
    output = await tool.run(ClickArgs(repere=mark.id), context(tmp_path))
    assert pointer.clicks == [(309, 159, (400, 200))]
    assert "Clic fait sur « le bouton Enregistrer » en (309, 159)" in output.text
    assert f"{mark.id}.jpg" in output.text  # la capture annotée, citée au journal
    clock.now += 601  # dix minutes plus tard, l'écran a pu changer
    with pytest.raises(NotFoundError, match="périmé"):
        await tool.run(ClickArgs(repere=mark.id), context(tmp_path))
    with pytest.raises(NotFoundError, match="inconnu"):
        await tool.run(ClickArgs(repere="r000000"), context(tmp_path))


async def test_finding_refuses_to_send_the_screen_when_it_must_stay_local(
    tmp_path: Path,
) -> None:
    sam = FakeSam({"button": BUTTONS})
    tool = FindOnScreen(locator(tmp_path, sam), lambda: "local")
    with pytest.raises(InvalidInputError, match="sur ce PC"):
        await tool.run(FindArgs(element="le bouton OK"), context(tmp_path))
    assert sam.concepts == []  # rien n'est parti chez SAM


async def test_finding_gives_a_mark_for_the_click(tmp_path: Path) -> None:
    sam = FakeSam({"button": BUTTONS})
    pick = {"numero": 1, "candidats": [], "vu": "Bouton Exporter en PDF"}
    tool = FindOnScreen(locator(tmp_path, sam, pick), lambda: "en_ligne")
    assert tool.level == Level.LIRE
    output = await tool.run(FindArgs(element="le bouton Exporter en PDF"), context(tmp_path))
    assert output.external  # ce que montre l'écran est une donnée
    assert output.data["repere"].startswith("r")
    assert (output.data["x"], output.data["y"]) == (69, 39)
    assert f"repere={output.data['repere']}" in output.text


def test_the_app_shows_the_annotated_capture_and_the_tools_need_pc_control(
    client: TestClient, services: Services
) -> None:
    assert client.get("/v1/desktop/marks/r123456").status_code == 404
    assert client.get("/v1/desktop/marks/..%2F..%2Fsecret").status_code == 404
    mark = services.marks.keep("le bouton OK", BUTTONS[0], window(), (400, 200), "")
    response = client.get(f"/v1/desktop/marks/{mark.id}")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.content.startswith(b"\xff\xd8")
    tools = {tool["name"]: tool for tool in client.get("/v1/tools").json()}
    assert (tools["trouver_a_l_ecran"]["level"], tools["cliquer_ecran"]["level"]) == (1, 2)
    assert tools["trouver_a_l_ecran"]["os_control"]
    assert tools["cliquer_ecran"]["os_control"]
