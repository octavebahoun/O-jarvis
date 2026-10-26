"""Le mode shell par l'API : lire tout de suite, agir après validation, puis continuer."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ojarvis.llm.providers.fake import FakeProvider, FakeReply, Script
from ojarvis.llm.structured import TOOL_NAME
from ojarvis.llm.types import LLMRequest, ToolCall
from ojarvis.services import Services
from tests.test_agent_chat import tool_messages, wait_plan
from tests.test_chat import read_sse


@pytest.fixture
def project(services: Services) -> Path:
    assert services.config.user_home is not None
    folder = services.config.user_home / "projet"
    folder.mkdir(parents=True)
    return folder


def shell_script(command: str) -> Script:
    """Le modèle lance une commande au premier tour, puis conclut."""

    def script(request: LLMRequest) -> FakeReply:
        if request.tools and request.tools[0].name == TOOL_NAME:
            return FakeReply(tool_calls=[ToolCall("m", TOOL_NAME, {"faits": []})])
        if "titre" in request.messages[0].content:
            return FakeReply(text="Session shell")
        if any(m.role == "tool" for m in request.messages) or "Résultats" in str(
            request.messages[-1].content
        ):
            return FakeReply(text="C'est fait, et vérifié.")
        call = ToolCall("c1", "executer_commande", {"commande": command, "dossier": "~/projet"})
        return FakeReply(text="Je lance la commande.", tool_calls=[call])

    return script


def shell_chat(client: TestClient, text: str, **extra: Any) -> list[tuple[str, dict[str, Any]]]:
    body = {"text": text, "channel": "shell", "folder": "~/projet", **extra}
    response = client.post("/v1/chat", json=body)
    assert response.status_code == 200, response.text
    return read_sse(response.text)


def test_a_read_runs_at_once_and_its_output_is_data(
    client: TestClient, llm: dict[str, FakeProvider], project: Path
) -> None:
    (project / "notes.txt").write_text("bonjour")
    llm["deepseek"]._script = shell_script("ls")
    events = shell_chat(client, "Qu'y a-t-il ici ?")
    tool = next(d for e, d in events if e == "tool" and d["status"] == "done")
    assert tool["description"] == "Lancer « ls » dans ~/projet"
    sent = tool_messages(llm["deepseek"])[0]
    assert sent.startswith('<donnees origine="commande">')
    assert "notes.txt" in sent
    assert "code de sortie 0" in sent
    first = llm["deepseek"].requests[0][0]
    assert "executer_commande" in {t.name for t in first.tools or []}
    assert "Mode shell" in first.messages[0].content
    assert "~/projet" in first.messages[0].content


def test_an_action_waits_then_jarvis_reads_the_result_and_continues(
    client: TestClient, llm: dict[str, FakeProvider], project: Path
) -> None:
    llm["deepseek"]._script = shell_script("touch rapport.txt")
    events = shell_chat(client, "Crée le fichier rapport.txt.")
    plan = next(d for e, d in events if e == "plan")
    assert plan["max_level"] == 2
    assert not (project / "rapport.txt").exists()  # rien avant ta validation

    assert client.post(f"/v1/plans/{plan['id']}/approve", json={}).status_code == 200
    assert wait_plan(client, plan["id"])["status"] == "done"
    assert (project / "rapport.txt").exists()
    # En mode shell, pas de compte rendu par le modèle : il lit lui-même la suite.
    systems = [r.messages[0].content for r, _ in llm["deepseek"].requests]
    assert not any("Tu viens d'exécuter un plan" in s for s in systems)

    conversation_id = events[0][1]["conversation_id"]
    shell_chat(client, "Continue.", conversation_id=conversation_id, after_plan=plan["id"])
    last = llm["deepseek"].requests[-1][0].messages[-1].content
    assert "Continue." in last
    assert "Résultats" in last
    assert '<donnees origine="commande">' in last
    assert "code de sortie 0" in last


def test_the_folder_goes_with_shell_mode_only(client: TestClient, project: Path) -> None:
    del project
    assert client.post("/v1/chat", json={"text": "x", "channel": "shell"}).status_code == 422
    assert client.post("/v1/chat", json={"text": "x", "folder": "~/projet"}).status_code == 422
    expected = {"~/.ssh": 422, "/etc": 422, "~/inexistant": 404}
    for folder, status in expected.items():
        body = {"text": "x", "channel": "shell", "folder": folder}
        assert client.post("/v1/chat", json=body).status_code == status


def test_a_plan_from_another_conversation_is_not_read(
    client: TestClient, llm: dict[str, FakeProvider], project: Path
) -> None:
    del project
    llm["deepseek"]._script = shell_script("touch a.txt")
    plan = next(d for e, d in shell_chat(client, "Crée a.txt.") if e == "plan")
    other = shell_chat(client, "Bonjour.")
    response = client.post(
        "/v1/chat",
        json={
            "text": "Continue.",
            "channel": "shell",
            "folder": "~/projet",
            "conversation_id": other[0][1]["conversation_id"],
            "after_plan": plan["id"],
        },
    )
    assert response.status_code == 404
