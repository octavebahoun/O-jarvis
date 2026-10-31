"""Avant / après (#15) : une action du mode shell se prouve par une mesure, prise avant et après."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from ojarvis.agent.plans import PLAN_TOOL
from ojarvis.agent.reports import SHELL_TOOL
from ojarvis.llm.providers.fake import FakeProvider, Script
from ojarvis.llm.types import ToolCall
from tests.shell.test_shell_chat import shell_chat, shell_script
from tests.test_agent_chat import scripted, tool_messages, wait_plan


def measured_plan(measure: dict[str, Any] | str) -> Script:
    steps = [
        {
            "outil": SHELL_TOOL,
            "arguments": {"commande": "touch preuve.txt", "dossier": "~/projet"},
            "pourquoi": "créer le fichier",
        }
    ]
    arguments = {"titre": "Créer preuve.txt", "etapes": steps, "mesure": measure}
    return scripted([[ToolCall("p", PLAN_TOOL, arguments)]])


def test_a_plan_measures_before_and_after_and_jarvis_must_compare(
    client: TestClient, llm: dict[str, FakeProvider], project: Path
) -> None:
    del project
    llm["deepseek"]._script = measured_plan({"commande": "ls", "dossier": "~/projet"})
    events = shell_chat(client, "Crée preuve.txt, et prouve-le.")
    plan = next(d for e, d in events if e == "plan")
    assert [s.get("measure") for s in plan["steps"]] == ["avant", None, "après"]
    assert client.post(f"/v1/plans/{plan['id']}/approve", json={}).status_code == 200
    final = wait_plan(client, plan["id"])
    assert final["status"] == "done"
    assert "preuve.txt" not in final["steps"][0]["result"]  # avant
    assert "preuve.txt" in final["steps"][2]["result"]  # après
    llm["deepseek"]._script = shell_script("ls")
    conversation_id = events[0][1]["conversation_id"]
    shell_chat(client, "Continue.", conversation_id=conversation_id, after_plan=[plan["id"]])
    shell = [r for r, _ in llm["deepseek"].requests if "Mode shell" in r.messages[0].content]
    last = next(m.content for m in reversed(shell[-1].messages) if m.role == "user")
    assert "Mesure avant (Lancer « ls » dans ~/projet)" in last
    assert "Mesure après (Lancer « ls » dans ~/projet)" in last
    assert "sinon dis « non vérifié » et ce qui manque, jamais « fait »" in last


def test_a_measure_written_as_text_works_in_the_folder_of_the_steps(
    client: TestClient, llm: dict[str, FakeProvider], project: Path
) -> None:
    del project
    llm["deepseek"]._script = measured_plan("ls")  # une simple chaîne, sans dossier
    plan = next(d for e, d in shell_chat(client, "Crée preuve.txt.") if e == "plan")
    assert plan["steps"][0]["description"] == "Lancer « ls » dans ~/projet"
    assert plan["steps"][0]["measure"] == "avant"


def test_a_measure_must_be_a_read(
    client: TestClient, llm: dict[str, FakeProvider], project: Path
) -> None:
    del project
    llm["deepseek"]._script = measured_plan({"commande": "rm -rf build", "dossier": "~/projet"})
    events = shell_chat(client, "Crée preuve.txt.")
    assert not [e for e, _ in events if e == "plan"]
    told = tool_messages(llm["deepseek"])[0]
    assert "La mesure d'un plan est une simple lecture" in told


def test_a_final_read_becomes_the_measure_taken_before_and_after(
    client: TestClient, llm: dict[str, FakeProvider], project: Path
) -> None:
    del project
    steps = [
        {
            "outil": SHELL_TOOL,
            "arguments": {"commande": "touch preuve.txt", "dossier": "~/projet"},
            "pourquoi": "créer",
        },
        {
            "outil": SHELL_TOOL,
            "arguments": {"commande": "ls -l preuve.txt", "dossier": "~/projet"},
            "pourquoi": "prouver",
        },
    ]
    llm["deepseek"]._script = scripted(
        [[ToolCall("p", PLAN_TOOL, {"titre": "Créer preuve.txt", "etapes": steps})]]
    )
    plan = next(d for e, d in shell_chat(client, "Crée preuve.txt.") if e == "plan")
    assert [(s.get("measure"), s["description"]) for s in plan["steps"]] == [
        ("avant", "Lancer « ls -l preuve.txt » dans ~/projet"),
        (None, "Lancer « touch preuve.txt » dans ~/projet"),
        ("après", "Lancer « ls -l preuve.txt » dans ~/projet"),
    ]


def test_an_action_without_proof_must_not_be_called_done(
    client: TestClient, llm: dict[str, FakeProvider], project: Path
) -> None:
    del project
    llm["deepseek"]._script = shell_script("touch rapport.txt")
    events = shell_chat(client, "Crée rapport.txt.")
    plan = next(d for e, d in events if e == "plan")
    assert client.post(f"/v1/plans/{plan['id']}/approve", json={}).status_code == 200
    wait_plan(client, plan["id"])
    conversation_id = events[0][1]["conversation_id"]
    shell_chat(client, "Continue.", conversation_id=conversation_id, after_plan=[plan["id"]])
    shell = [r for r, _ in llm["deepseek"].requests if "Mode shell" in r.messages[0].content]
    last = next(m.content for m in reversed(shell[-1].messages) if m.role == "user")
    assert "Ce plan n'avait pas de mesure : ne dis pas « fait »." in last
