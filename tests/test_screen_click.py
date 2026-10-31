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
from ojarvis.desktop.locate import (
    AmbiguousTargetError,
    Locator,
    ScreenMarks,
    reading_order,
    unchanged,
)
from ojarvis.desktop.pointer import Stream, to_stream
from ojarvis.desktop.sam import Box, Segmentation, parse_boxes
from ojarvis.llm.catalog import Catalog
from ojarvis.llm.providers.fake import FakeProvider, FakeReply
from ojarvis.llm.router import ModelRouter
from ojarvis.llm.structured import TOOL_NAME
from ojarvis.llm.types import LLMRequest, ToolCall
from ojarvis.services import Services
from ojarvis.tools.base import Level, ToolContext
from ojarvis.tools.screen import ClickArgs, ClickOnScreen, FindArgs, FindOnScreen

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
    def __init__(self, found: dict[str, tuple[Box, ...]], under: Box | None = None) -> None:
        self.found = found
        self.under = under
        self.concepts: list[str] = []
        self.points: list[tuple[int, int]] = []

    async def find(self, image: bytes, concept: str, *, mime: str = "image/png") -> Segmentation:
        del image, mime
        self.concepts.append(concept)
        return Segmentation(self.found.get(concept, ()), 400, 200)

    async def at_point(
        self, image: bytes, x: int, y: int, size: tuple[int, int], *, mime: str = "image/png"
    ) -> Segmentation:
        del image, size, mime
        self.points.append((x, y))
        return Segmentation((self.under,) if self.under else (), 400, 200)


#: Muse Spark désigne à peu près l'endroit : la zone autour contient les deux boutons.
POINTED = {"trouve": True, "x": 189, "y": 99}


def muse(*answers: dict[str, Any]) -> tuple[ModelRouter, FakeProvider]:
    """Muse Spark (profil « Tout Meta ») qui répond, dans l'ordre : l'endroit désigné, un
    choix sur la zone agrandie, puis son avis sur le zoom, et ainsi de suite."""
    left = list(answers)

    def script(request: LLMRequest) -> FakeReply:
        assert request.messages[-2].images  # il voit la capture (numérotée, ou agrandie)
        return FakeReply(tool_calls=[ToolCall("p", TOOL_NAME, left.pop(0))])

    provider = FakeProvider("meta", script=script)
    router = ModelRouter({"meta": provider}, Catalog(), profile=lambda: "meta")
    return router, provider


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def locator(
    tmp_path: Path, sam: FakeSam, *answers: dict[str, Any], clock: Clock | None = None
) -> Locator:
    router, _ = muse(*answers)
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


CONFIRMED = {"bon": True, "vu": "Bouton Enregistrer"}


def test_boxes_are_numbered_in_reading_order_without_duplicates() -> None:
    icon = Box(7, 22, 21, 118, 58)  # le même bouton, vu aussi comme une icône
    right, left = Box(3, 300, 25, 330, 55), Box(4, 150, 30, 180, 50)
    assert reading_order([BUTTONS[1], right, BUTTONS[0], icon, left]) == [
        BUTTONS[0],
        left,
        right,
        BUTTONS[1],
    ]


async def test_sam_frames_and_muse_spark_points_picks_then_checks(tmp_path: Path) -> None:
    # SAM cherche boutons et icônes en même temps ; Muse Spark désigne la zone, y choisit,
    # puis revoit son choix sur un zoom.
    sam = FakeSam({"button": BUTTONS})
    found = await locator(
        tmp_path, sam, POINTED, {"numero": 2, "candidats": [], "vu": "en bas"}, CONFIRMED
    ).find("le bouton Enregistrer", "bouton")
    assert found.box == BUTTONS[1]
    assert found.center == (309, 159)
    assert (found.width, found.height) == (400, 200)
    assert found.seen == "Bouton Enregistrer"
    assert found.image.is_file()
    assert found.image.stat().st_mode & 0o777 == 0o600  # une capture de ton écran
    assert sorted(sam.concepts) == ["button", "icon"]


async def test_a_choice_the_zoom_denies_is_picked_again_then_asked(tmp_path: Path) -> None:
    """Le 30/10, Muse Spark avait pris l'icône voisine du bouton PDF dans LibreOffice."""
    sam = FakeSam({"button": BUTTONS})
    router, provider = muse(
        POINTED,
        {"numero": 1, "candidats": []},
        {"bon": False, "vu": "l'icône Imprimer"},
        {"numero": 2, "candidats": []},
        CONFIRMED,
    )
    marks = ScreenMarks(tmp_path / "reperes", clock=Clock())

    async def grab() -> bytes:
        return window()

    found = await Locator(sam, router, marks, grab).find("le bouton PDF", "bouton")  # type: ignore[arg-type]
    assert found.box == BUTTONS[1]
    second_pick = provider.requests[3][0].messages[-2].content
    assert "Ce n'est pas le bon : le n° 1 est l'icône Imprimer." in second_pick
    # Deux démentis : Jarvis demande, avec ce qu'il a vu de plus proche.
    sam_again = FakeSam({"button": BUTTONS})
    finder = locator(
        tmp_path,
        sam_again,
        POINTED,
        {"numero": 1, "candidats": []},
        {"bon": "non", "vu": "l'icône Imprimer"},
        {"numero": 2, "candidats": []},
        {"bon": False, "vu": "l'icône Aperçu"},
    )
    with pytest.raises(AmbiguousTargetError, match="Le plus proche était : l'icône Imprimer"):
        await finder.find("le bouton PDF", "bouton")


async def test_nothing_found_asks_instead_of_guessing(tmp_path: Path) -> None:
    # Muse Spark ne voit pas l'élément sur la capture.
    unseen = locator(tmp_path, FakeSam({"icon": BUTTONS}), {"trouve": "non"})
    with pytest.raises(AmbiguousTargetError, match="Je ne vois pas « le bouton Supprimer »"):
        await unseen.find("le bouton Supprimer", "bouton")
    # Il le désigne, mais n'en reconnaît aucun dans la zone (« null » écrit en texte), et SAM
    # ne voit rien sous le point.
    sam = FakeSam({"icon": BUTTONS})
    finder = locator(tmp_path, sam, POINTED, {"numero": "null", "candidats": []})
    with pytest.raises(AmbiguousTargetError, match="Je ne trouve pas « le bouton Supprimer »"):
        await finder.find("le bouton Supprimer", "bouton")
    assert sam.points == [(189, 99)]
    nothing = locator(tmp_path, FakeSam({}), POINTED)  # SAM ne voit rien dans la zone
    with pytest.raises(AmbiguousTargetError, match="cherché comme : button, icon"):
        await nothing.find("le bouton Supprimer", "bouton")


async def test_an_element_sam_did_not_frame_is_framed_from_the_pointed_spot(
    tmp_path: Path,
) -> None:
    # Une lettre « B » n'est ni un bouton ni une icône pour SAM : il encadre l'objet sous
    # le point désigné par Muse Spark, et le zoom confirme (essai du 31/10, bouton Gras).
    letter = Box(1, 300, 150, 312, 166)
    sam = FakeSam({}, under=letter)
    found = await locator(
        tmp_path, sam, {"trouve": True, "x": 306, "y": 158}, {"bon": True, "vu": "lettre B"}
    ).find("le bouton Gras", "bouton")
    assert found.box == letter
    assert sam.points == [(306, 158)]


async def test_two_possible_elements_ask_which_one_without_numbers(tmp_path: Path) -> None:
    sam = FakeSam({"button": BUTTONS})
    both = {
        "numero": 1,
        "candidats": [{"numero": 1, "ou": "en haut à gauche"}, {"numero": 2, "ou": "en bas"}],
    }
    with pytest.raises(AmbiguousTargetError, match="en haut à gauche ; en bas") as raised:
        await locator(tmp_path, sam, POINTED, both).find("le bouton Exporter", "bouton")
    assert "n°" not in raised.value.message  # l'utilisateur ne voit aucun numéro


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


async def test_a_click_only_happens_on_a_recent_mark_once_it_is_visible(tmp_path: Path) -> None:
    clock = Clock()
    marks = ScreenMarks(tmp_path / "reperes", clock=clock)
    mark = marks.keep("le bouton Enregistrer", BUTTONS[1], window(), (400, 200), "")
    screens = [window(covered=True)]

    async def grab() -> bytes:
        return screens.pop(0) if len(screens) > 1 else screens[0]

    pointer = FakePointer()
    tool = ClickOnScreen(marks, pointer, grab, wait_s=0.05, poll_s=0.01)
    assert tool.level == Level.AGIR
    assert tool.describe(ClickArgs(repere=mark.id)) == (
        f"Cliquer sur le bouton Enregistrer (repère {mark.id})"
    )
    # Une fenêtre reste devant : rien n'est cliqué.
    with pytest.raises(InvalidInputError, match="rien n'a été cliqué"):
        await tool.run(ClickArgs(repere=mark.id), context(tmp_path))
    assert pointer.clicks == []
    # À la validation, la fenêtre de Jarvis est devant, puis se réduit : le clic attend de
    # revoir l'élément (le 31/10, il était refusé aussitôt).
    screens[:] = [window(covered=True), window(covered=True), window()]
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
    pick = {"numero": 1, "candidats": [], "vu": "en haut"}
    check = {"bon": True, "vu": "Bouton Exporter en PDF"}
    waits: list[float] = []

    async def sleep(seconds: float) -> None:
        waits.append(seconds)

    tool = FindOnScreen(locator(tmp_path, sam, POINTED, pick, check), lambda: "en_ligne", sleep)
    assert tool.level == Level.LIRE
    args = FindArgs(element="le bouton Exporter en PDF", attendre_s=3)
    output = await tool.run(args, context(tmp_path))
    assert waits == [3]  # « attends 3 s » : le temps de mettre la fenêtre devant
    assert output.external  # ce que montre l'écran est une donnée
    assert output.data["repere"].startswith("r")
    assert (output.data["x"], output.data["y"]) == (69, 39)
    assert f"repere={output.data['repere']}" in output.text
    assert "sans redemander" in output.text  # le plan, aussitôt


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
