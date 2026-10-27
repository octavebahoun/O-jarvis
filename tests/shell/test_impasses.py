"""La mémoire des impasses : notée un jour, retrouvée le lendemain, jamais injectée d'office."""

from __future__ import annotations

from functools import partial
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ojarvis.llm.providers.fake import FakeProvider, FakeReply, Script
from ojarvis.llm.structured import TOOL_NAME
from ojarvis.llm.types import LLMRequest, ToolCall
from ojarvis.services import Services
from tests.shell.test_shell_chat import shell_chat
from tests.test_agent_chat import tool_messages
from tests.test_chat import read_sse

ERROR = "test_liste_vide : ZeroDivisionError dans moyenne"
NOTE = {
    "dossier": "~/projet",
    "erreur": ERROR,
    "tentative": "renvoyer None quand la liste est vide",
    "raison": "le test attend 0, pas None",
}


@pytest.fixture
def projects(services: Services) -> Path:
    assert services.config.user_home is not None
    for name in ("projet", "autre"):
        (services.config.user_home / name).mkdir(parents=True)
    return services.config.user_home


def calling(tool: str, arguments: dict[str, Any]) -> Script:
    """Le modèle appelle un outil, puis conclut."""

    def script(request: LLMRequest) -> FakeReply:
        if request.tools and request.tools[0].name == TOOL_NAME:
            return FakeReply(tool_calls=[ToolCall("m", TOOL_NAME, {"faits": []})])
        if "titre" in request.messages[0].content:
            return FakeReply(text="Impasses")
        if any(m.role == "tool" for m in request.messages):
            return FakeReply(text="Compris.")
        return FakeReply(text="Je regarde.", tool_calls=[ToolCall("i", tool, arguments)])

    return script


def test_an_impasse_noted_today_is_found_in_another_session(
    client: TestClient, llm: dict[str, FakeProvider], projects: Path
) -> None:
    del projects
    llm["deepseek"]._script = calling("noter_impasse", NOTE)
    shell_chat(client, "Le correctif n'a pas marché.")
    noted = client.get("/v1/memory", params={"kind": "impasse"}).json()
    assert [m["content"] for m in noted] == [
        "[~/projet] Erreur : test_liste_vide : ZeroDivisionError dans moyenne — Tenté : "
        "renvoyer None quand la liste est vide — Échec : le test attend 0, pas None"
    ]
    # Une autre session (nouvelle conversation) : le test échoue de nouveau.
    consult = {"dossier": "~/projet", "erreur": "ZeroDivisionError dans test_liste_vide"}
    llm["deepseek"]._script = calling("consulter_impasses", consult)
    shell_chat(client, "Les tests échouent encore.")
    found = tool_messages(llm["deepseek"])[-1]
    assert found.startswith("Impasses déjà notées dans ~/projet (ne retente pas ces correctifs)")
    assert "Tenté : renvoyer None quand la liste est vide" in found


def test_another_project_does_not_see_the_impasse(
    client: TestClient, llm: dict[str, FakeProvider], projects: Path
) -> None:
    del projects
    llm["deepseek"]._script = calling("noter_impasse", NOTE)
    shell_chat(client, "Le correctif n'a pas marché.")
    consult = {"dossier": "~/autre", "erreur": "ZeroDivisionError dans test_liste_vide"}
    llm["deepseek"]._script = calling("consulter_impasses", consult)
    shell_chat(client, "Les tests échouent.", folder="~/autre")
    assert tool_messages(llm["deepseek"])[-1] == (
        "Aucune impasse notée pour cette erreur dans ~/autre."
    )


def test_an_impasse_never_enters_the_context_on_its_own(
    client: TestClient, llm: dict[str, FakeProvider], projects: Path
) -> None:
    del projects
    llm["deepseek"]._script = calling("noter_impasse", NOTE)
    shell_chat(client, "Le correctif n'a pas marché.")
    sent_before = len(llm["deepseek"].requests)
    # La même erreur, dite dans le chat : l'impasse reste dans sa mémoire, hors du contexte.
    llm["deepseek"]._script = calling("consulter_impasses", {"dossier": "~/projet", "erreur": "x"})
    events = read_sse(client.post("/v1/chat", json={"text": f"Pourquoi {ERROR} ?"}).text)
    context = next(d for e, d in events if e == "context")
    assert all("Tenté" not in item["preview"] for item in context["items"])
    later = [r for r, _ in llm["deepseek"].requests[sent_before:]]
    assert all("renvoyer None" not in (m.content or "") for r in later for m in r.messages)


def test_an_impasse_can_be_deleted_like_any_memory(
    client: TestClient, llm: dict[str, FakeProvider], projects: Path
) -> None:
    del projects
    llm["deepseek"]._script = calling("noter_impasse", NOTE)
    shell_chat(client, "Le correctif n'a pas marché.")
    noted = client.get("/v1/memory", params={"kind": "impasse"}).json()
    assert client.delete(f"/v1/memory/{noted[0]['id']}").status_code == 204
    assert client.get("/v1/memory", params={"kind": "impasse"}).json() == []


def test_the_impasse_tools_exist_only_in_shell_mode(
    client: TestClient, llm: dict[str, FakeProvider], projects: Path
) -> None:
    del projects
    llm["deepseek"]._script = calling("noter_impasse", NOTE)
    events = read_sse(client.post("/v1/chat", json={"text": "Note l'impasse."}).text)
    failed = [d for e, d in events if e == "tool" and d["status"] == "failed"]
    assert "mode shell" in failed[0]["error"]
    assert client.get("/v1/memory", params={"kind": "impasse"}).json() == []


def test_impasses_that_look_alike_stay_apart(client: TestClient, services: Services) -> None:
    # Revue du 27/10/2026 : à 0,97 de ressemblance, la seconde écrasait la première.
    error = (
        "test_liste_vide échoue : ZeroDivisionError, division par zéro dans moyenne quand "
        "la liste des notes reçues est vide après le filtrage des absences du trimestre"
    )
    first = f"[~/projet] Erreur : {error} — Tenté : renvoyer None — Échec : le test attend zéro"
    contents = [
        first,
        first.replace("None", "NaN"),  # un autre correctif tenté
        first.replace("[~/projet]", "[~/autre]"),  # la même erreur dans un autre projet
    ]
    assert client.portal is not None
    for content in contents:
        _record, created = client.portal.call(partial(services.memory.add, "impasse", content))
        assert created
    assert len(client.get("/v1/memory", params={"kind": "impasse"}).json()) == 3
    # La même impasse notée une seconde fois reste une seule.
    _record, created = client.portal.call(partial(services.memory.add, "impasse", first))
    assert not created
