"""Un geste décrit en mots, retrouvé sur la page au moment de le faire : plusieurs gestes dans
un seul plan (ouvrir la zone de commentaire, y écrire, publier), validé une fois."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from ojarvis.llm.catalog import Catalog
from ojarvis.llm.providers.fake import FakeProvider, FakeReply
from ojarvis.llm.router import ModelRouter
from ojarvis.llm.structured import TOOL_NAME
from ojarvis.llm.types import LLMRequest, ToolCall
from ojarvis.tools.base import Level

import ojarvis_shell.llm.meta_profile  # noqa: F401 - « Tout Meta »
from ojarvis_shell.chrome.bridge import ChromeBridge, TabAction, TabResult
from ojarvis_shell.tools import tab_step
from ojarvis_shell.tools.tab import TabActionError
from ojarvis_shell.tools.tab_step import StepArgs, TabStep

FEED: dict[str, Any] = {
    "url": "https://www.linkedin.com/feed/",
    "title": "Fil d'actualité",
    "text": "Ada Martin : mon premier robot. Linus Weber : un nouveau logo.",
    "elements": [
        {"ref": 4, "kind": "bouton", "label": "Commenter", "zone": "Post de Ada Martin"},
        {"ref": 9, "kind": "bouton", "label": "Commenter", "zone": "Post de Linus Weber"},
        {"ref": 12, "kind": "champ", "label": "Éditeur de texte pour créer un commentaire"},
    ],
}


@pytest.fixture(autouse=True)
def no_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tab_step, "SETTLE_S", 0.0)


def picking(ref: int | None) -> tuple[ModelRouter, list[str]]:
    """Muse Spark choisit l'élément : il rend seulement un numéro."""
    asked: list[str] = []

    def script(request: LLMRequest) -> FakeReply:
        asked.append(request.messages[1].content)
        return FakeReply(tool_calls=[ToolCall("p", TOOL_NAME, {"ref": ref})])

    providers: dict[str, Any] = {"meta": FakeProvider("meta", script=script)}
    return ModelRouter(providers, Catalog(), profile=lambda: "meta"), asked


async def run(tool: TabStep, bridge: ChromeBridge, args: StepArgs) -> tuple[Any, list[TabAction]]:
    """Le panneau de Chrome : il rend la page lue, puis fait le geste."""
    done: list[TabAction] = []

    async def extension() -> None:
        while (action := await bridge.next(1.0)) is not None:
            if action.kind == "lire":
                bridge.resolve(action.id, TabResult(True, "ok", data={"page": FEED}))
                continue
            done.append(action)
            bridge.resolve(action.id, TabResult(True, "Fait."))
            return

    serving = asyncio.create_task(extension())
    await asyncio.sleep(0)
    context: Any = SimpleNamespace()
    try:
        out = await tool.run(args, context)
    finally:
        serving.cancel()
    return out, done


async def test_the_button_is_found_on_the_page_as_it_is_at_that_moment() -> None:
    router, asked = picking(9)
    bridge = ChromeBridge()
    args = StepArgs(onglet=7, geste="cliquer", cible="le bouton Commenter du post de Linus")
    out, done = await run(TabStep(bridge, router), bridge, args)
    assert [(a.kind, a.args) for a in done] == [
        ("cliquer", {"onglet": 7, "ref": 9, "cible": "Commenter"})
    ]
    assert "(dans « Post de Linus Weber »)" in asked[0]  # le bloc de chaque bouton
    assert "le bouton Commenter du post de Linus" in asked[0]
    assert '<donnees origine="https://www.linkedin.com/feed/">' in asked[0]
    assert "Post de Linus Weber" in out.text
    assert out.external


async def test_the_text_written_is_the_one_approved_in_the_plan() -> None:
    router, _ = picking(12)
    bridge = ChromeBridge()
    comment = "Bravo Linus, ce logo est très lisible."
    args = StepArgs(onglet=7, geste="remplir", cible="le champ de commentaire", valeur=comment)
    _, done = await run(TabStep(bridge, router), bridge, args)
    assert done[0].kind == "remplir"
    assert done[0].args["valeur"] == comment  # le modèle choisit l'endroit, jamais le texte
    assert done[0].args["ref"] == 12


async def test_nothing_is_done_when_no_element_matches() -> None:
    router, _ = picking(None)
    bridge = ChromeBridge()
    args = StepArgs(onglet=7, geste="cliquer", cible="le bouton Envoyer un message")
    with pytest.raises(TabActionError, match="rien n'a été fait"):
        await run(TabStep(bridge, router), bridge, args)


def test_a_risky_step_is_level_three_and_the_plan_reads_in_words() -> None:
    router, _ = picking(None)
    tool = TabStep(ChromeBridge(), router)
    pay = StepArgs(onglet=7, geste="cliquer", cible="le bouton Payer la commande")
    assert tool.level_for(pay) == Level.DETRUIRE
    write = StepArgs(onglet=7, geste="remplir", cible="le champ de commentaire", valeur="Bravo")
    assert tool.level_for(write) == Level.AGIR
    assert tool.describe(write) == "Écrire « Bravo » dans le champ de commentaire (onglet n° 7)"
