"""Agir dans ton onglet Chrome : un plan validé passe par le pont, le panneau fait l'action."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ojarvis.agent.plans import PLAN_TOOL
from ojarvis.chrome.bridge import ChromeBridge, TabAction, TabResult
from ojarvis.core.errors import UnavailableError
from ojarvis.llm.providers.fake import FakeProvider
from ojarvis.llm.types import ToolCall
from ojarvis.tools.base import Level
from ojarvis.tools.tab import ElementArgs, FillTabArgs, TabClick, TabFill
from tests.test_agent_chat import scripted, wait_plan
from tests.test_chat import read_sse

PAGE: dict[str, Any] = {
    "url": "https://boutique.exemple/the",
    "title": "Thés",
    "text": "Thé vert sencha : 4,50 €",
    "elements": [
        {"ref": 1, "kind": "bouton", "label": "Ajouter au panier"},
        {"ref": 2, "kind": "lien", "label": "Contact", "href": "https://boutique.exemple/contact"},
    ],
}
CLICK = {
    "outil": "onglet_cliquer",
    "arguments": {"ref": 1, "cible": "Ajouter au panier"},
    "pourquoi": "ajouter le thé",
}


def test_sensitive_targets_are_level_three() -> None:
    click = TabClick(ChromeBridge())
    assert click.level_for(ElementArgs(ref=1, cible="Ajouter au panier")) == Level.AGIR
    assert click.level_for(ElementArgs(ref=2, cible="Payer 12 €")) == Level.DETRUIRE
    assert click.level_for(ElementArgs(ref=3, cible="Supprimer le compte")) == Level.DETRUIRE
    fill = TabFill(ChromeBridge())
    assert fill.level_for(FillTabArgs(ref=4, cible="Recherche", valeur="thé")) == Level.AGIR
    assert fill.level_for(FillTabArgs(ref=5, cible="Numéro de carte", valeur="4970")) == 3


async def test_the_bridge_needs_an_open_panel_and_returns_its_result() -> None:
    bridge = ChromeBridge()
    with pytest.raises(UnavailableError, match="panneau de Jarvis est fermé"):
        await bridge.run(TabAction("cliquer", {"ref": 1}, 2))
    assert await bridge.next(0.01) is None  # le panneau passe : il est ouvert

    async def panel() -> None:
        action = await bridge.next(1.0)
        assert action is not None
        assert bridge.resolve(action.id, TabResult(True, "Cliqué."))

    task = asyncio.create_task(panel())
    assert await bridge.run(TabAction("cliquer", {"ref": 1}, 2)) == TabResult(True, "Cliqué.")
    await task
    assert not bridge.resolve("inconnue", TabResult(True, ""))
    with pytest.raises(UnavailableError, match="pas répondu"):
        await bridge.run(TabAction("cliquer", {"ref": 1}, 2), timeout_s=0.05)


def test_a_validated_plan_is_done_by_the_panel_in_the_tab(
    client: TestClient, llm: dict[str, FakeProvider]
) -> None:
    llm["deepseek"]._script = scripted(
        [[ToolCall("p", PLAN_TOOL, {"titre": "Ajouter le thé", "etapes": [CLICK]})]],
        "Je te propose de l'ajouter.",
    )
    body = {"text": "Ajoute le thé au panier.", "channel": "chrome", "page": PAGE}
    events = read_sse(client.post("/v1/chat", json=body).text)
    plan = next(data for kind, data in events if kind == "plan")
    assert plan["steps"][0]["tool"] == "onglet_cliquer"
    assert plan["tainted"]  # proposé après la lecture de la page
    sent = llm["deepseek"].requests[0][0].messages[-1].content
    assert "[1] bouton « Ajouter au panier »" in sent
    assert client.get("/v1/chrome/actions/next", params={"wait": 0}).status_code == 204
    client.post(f"/v1/plans/{plan['id']}/approve", json={})
    action = client.get("/v1/chrome/actions/next", params={"wait": 5}).json()
    assert action["kind"] == "cliquer"
    assert action["args"] == {"ref": 1, "cible": "Ajouter au panier"}
    result = {"ok": True, "text": "Cliqué sur « Ajouter au panier »."}
    assert client.post(f"/v1/chrome/actions/{action['id']}", json=result).status_code == 204
    done = wait_plan(client, plan["id"])
    assert done["status"] == "done"
    assert client.post(f"/v1/chrome/actions/{action['id']}", json=result).status_code == 404


def test_tab_tools_stay_in_the_chrome_panel(
    client: TestClient, llm: dict[str, FakeProvider]
) -> None:
    llm["deepseek"]._script = scripted(
        [[ToolCall("p", PLAN_TOOL, {"titre": "Cliquer", "etapes": [CLICK]})]], "D'accord."
    )
    events = read_sse(client.post("/v1/chat", json={"text": "Clique sur le bouton."}).text)
    offered = {tool.name for tool in llm["deepseek"].requests[0][0].tools}
    assert "onglet_cliquer" not in offered
    assert not [data for kind, data in events if kind == "plan"]  # refusé hors du panneau
