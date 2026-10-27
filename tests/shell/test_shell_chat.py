"""Le mode shell par l'API : lire tout de suite, agir après validation, puis continuer."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ojarvis.agent.loop import ROUNDS_EXHAUSTED, from_web
from ojarvis.chat.service import SHELL_TOOL_ROUNDS
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


def test_every_command_goes_to_the_audit_log_reads_included(
    client: TestClient, llm: dict[str, FakeProvider], project: Path
) -> None:
    del project
    llm["deepseek"]._script = shell_script("ls")
    shell_chat(client, "Qu'y a-t-il ici ?")
    audit = client.get("/v1/audit").json()
    entries = [(i["action"], i["level"], i["summary"]) for i in reversed(audit["items"])]
    assert entries == [
        ("outil.executer_commande", 1, "Lancé : Lancer « ls » dans ~/projet"),
        ("outil.executer_commande.fait", 1, "Fait : Lancer « ls » dans ~/projet"),
    ]
    assert client.post("/v1/audit/verify").json()["ok"] is True


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
    # La dernière requête du mode shell : l'extraction des souvenirs, en arrière-plan, peut
    # arriver après elle.
    shell = [r for r, _ in llm["deepseek"].requests if "Mode shell" in r.messages[0].content]
    last = shell[-1].messages[-1].content
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


# --------------------------------------------------------------------------- retouches de code

CODE = "def moyenne(notes):\n    return sum(notes) / len(notes)\n"
FIX = {
    "chemin": "~/projet/stats.py",
    "ancien": "    return sum(notes) / len(notes)",
    "nouveau": "    return sum(notes) / len(notes) if notes else 0",
}


def edit_script(arguments: dict[str, Any], verdict: str) -> Script:
    """L'auteur propose une retouche ; le relecteur répond `verdict`."""

    def script(request: LLMRequest) -> FakeReply:
        system = request.messages[0].content
        if request.tools and request.tools[0].name == TOOL_NAME:
            return FakeReply(tool_calls=[ToolCall("m", TOOL_NAME, {"faits": []})])
        if "titre" in system:
            return FakeReply(text="Retouche")
        if "Tu relis une modification de code" in system:
            return FakeReply(text=verdict)
        if any(m.role == "tool" for m in request.messages):
            return FakeReply(text="Voilà la retouche, avec l'avis du relecteur.")
        return FakeReply(
            text="Je corrige.", tool_calls=[ToolCall("e1", "modifier_fichier", arguments)]
        )

    return script


def test_a_code_edit_shows_its_diff_and_the_reviewer_verdict(
    client: TestClient, llm: dict[str, FakeProvider], project: Path
) -> None:
    (project / "stats.py").write_text(CODE)
    llm["deepseek"]._script = edit_script(FIX, "PROBLÈME\nLa division par zéro reste possible.")
    events = shell_chat(client, "Corrige la moyenne d'une liste vide.")
    plan = next(d for e, d in events if e == "plan")
    preview = plan["steps"][0]["preview"]
    assert "-    return sum(notes) / len(notes)" in preview
    assert "+    return sum(notes) / len(notes) if notes else 0" in preview
    # Mode shell : la tâche est « reasoning » ; le relecteur passe par la tâche « review ».
    assert plan["review"] == {
        "ok": False,
        "notes": "La division par zéro reste possible.",
        "provider": "deepseek",
        "model": "deepseek-v4-pro",
    }
    told = tool_messages(llm["deepseek"])[0]
    assert "Relecture (deepseek / deepseek-v4-pro) : problème." in told
    assert (project / "stats.py").read_text() == CODE  # rien avant ta validation


def test_an_edit_that_would_not_apply_goes_back_to_the_model(
    client: TestClient, llm: dict[str, FakeProvider], project: Path
) -> None:
    (project / "stats.py").write_text(CODE)
    wrong = {**FIX, "ancien": "    return 42"}
    llm["deepseek"]._script = edit_script(wrong, "OK")
    events = shell_chat(client, "Corrige la moyenne.")
    assert not [e for e, _ in events if e == "plan"]
    told = tool_messages(llm["deepseek"])[0]
    assert told.startswith("Échec : Plan refusé : Le passage à remplacer est introuvable")


def test_in_the_app_the_diff_is_shown_without_a_review(
    client: TestClient, llm: dict[str, FakeProvider], project: Path
) -> None:
    (project / "stats.py").write_text(CODE)
    llm["deepseek"]._script = edit_script(FIX, "OK")
    response = client.post("/v1/chat", json={"text": "Corrige la moyenne."})
    plan = next(d for e, d in read_sse(response.text) if e == "plan")
    assert "+    return sum(notes) / len(notes) if notes else 0" in plan["steps"][0]["preview"]
    assert "review" not in plan


def test_at_the_step_limit_jarvis_stops_and_says_where_it_is(
    client: TestClient, llm: dict[str, FakeProvider], project: Path
) -> None:
    del project

    def endless(request: LLMRequest) -> FakeReply:
        if request.tools and request.tools[0].name == TOOL_NAME:
            return FakeReply(tool_calls=[ToolCall("m", TOOL_NAME, {"faits": []})])
        if "titre" in request.messages[0].content:
            return FakeReply(text="Sans fin")
        if not request.tools:
            return FakeReply(text="J'ai fait 16 étapes ; il reste à relancer les tests.")
        call = ToolCall("c", "executer_commande", {"commande": "ls", "dossier": "~/projet"})
        return FakeReply(text="Encore une.", tool_calls=[call])

    llm["deepseek"]._script = endless
    events = shell_chat(client, "Vérifie tout, encore et encore.")
    ran = [d for e, d in events if e == "tool" and d["status"] == "done"]
    assert len(ran) == SHELL_TOOL_ROUNDS
    last = [r for r, _ in llm["deepseek"].requests if "Mode shell" in r.messages[0].content][-1]
    assert not last.tools  # le dernier appel ne propose plus d'outil
    assert last.messages[-1].content == ROUNDS_EXHAUSTED
    assert events[-1][0] == "done"
    text = "".join(d["text"] for e, d in events if e == "delta")
    assert text.endswith("il reste à relancer les tests.")


def test_the_plan_tells_the_terminal_the_command_and_whether_the_web_was_read(
    client: TestClient, llm: dict[str, FakeProvider], project: Path
) -> None:
    del project
    llm["deepseek"]._script = shell_script("touch rapport.txt")
    plan = next(d for e, d in shell_chat(client, "Crée rapport.txt.") if e == "plan")
    assert plan["steps"][0]["tool"] == "executer_commande"
    assert plan["steps"][0]["command"] == "touch rapport.txt"
    assert plan["web"] is False


@pytest.mark.parametrize(
    ("source", "web"),
    [
        ("https://exemple.com/article", True),
        ("recherche web", True),
        ("http://localhost:5173/", False),  # ta page en développement
        ("commande", False),
        ("~/projet/stats.py", False),
        (None, False),
    ],
)
def test_what_counts_as_read_from_the_web(source: str | None, web: bool) -> None:
    assert from_web(source) is web
