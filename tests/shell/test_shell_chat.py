"""Le mode shell par l'API : lire tout de suite, agir après validation, puis continuer."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ojarvis.agent.loop import ROUNDS_EXHAUSTED, SHELL_TOOL, claims_missing_plan, from_web
from ojarvis.agent.plans import PLAN_TOOL
from ojarvis.chat.service import SHELL_TOOL_ROUNDS
from ojarvis.llm.providers.fake import FakeProvider, FakeReply, Script
from ojarvis.llm.structured import TOOL_NAME
from ojarvis.llm.types import LLMRequest, ToolCall
from ojarvis.services import Services
from tests.test_agent_chat import scripted, tool_messages, wait_plan
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


def test_a_follow_up_keeps_the_request_and_the_marks_of_what_was_read(
    client: TestClient, llm: dict[str, FakeProvider], project: Path, services: Services
) -> None:
    del project
    llm["deepseek"]._script = shell_script("curl --version > version.txt")
    events = shell_chat(client, "Note la version de curl.")
    plan = next(d for e, d in events if e == "plan")
    assert client.post(f"/v1/plans/{plan['id']}/approve", json={}).status_code == 200
    assert wait_plan(client, plan["id"])["status"] == "done"

    def again(request: LLMRequest) -> FakeReply:
        if request.tools and request.tools[0].name == TOOL_NAME:
            return FakeReply(tool_calls=[ToolCall("m", TOOL_NAME, {"faits": []})])
        if "titre" in request.messages[0].content:
            return FakeReply(text="Session shell")
        if any(m.role == "tool" for m in request.messages):
            return FakeReply(text="J'attends ta validation.")
        arguments = {"commande": "touch annexe.txt", "dossier": "~/projet"}
        return FakeReply(text="Et l'annexe.", tool_calls=[ToolCall("c2", SHELL_TOOL, arguments)])

    llm["deepseek"]._script = again
    conversation_id = events[0][1]["conversation_id"]
    follow = shell_chat(
        client, "Continue.", conversation_id=conversation_id, after_plan=[plan["id"]]
    )
    second = next(d for e, d in follow if e == "plan")
    # Les sorties revenues restent des données, et curl a parlé au réseau : pas de « toujours ».
    assert second["tainted"] is True
    assert second["web"] is True
    # La suite n'a pour texte que « Continue. » : le plan garde la demande d'origine.
    assert client.portal is not None
    stored = client.portal.call(services.plans.get, second["id"])
    assert stored.request == "Note la version de curl."


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
        if request.messages[-1].content == ROUNDS_EXHAUSTED:
            return FakeReply(text="J'ai fait 16 étapes ; il reste à relancer les tests.")
        call = ToolCall("c", "executer_commande", {"commande": "ls", "dossier": "~/projet"})
        return FakeReply(text="", tool_calls=[call])  # des outils, sans un mot

    llm["deepseek"]._script = endless
    events = shell_chat(client, "Vérifie tout, encore et encore.")
    ran = [d for e, d in events if e == "tool" and d["status"] == "done"]
    assert len(ran) == SHELL_TOOL_ROUNDS
    last = [r for r, _ in llm["deepseek"].requests if "Mode shell" in r.messages[0].content][-1]
    assert last.messages[-1].content == ROUNDS_EXHAUSTED
    # Aucun message vide sans appel d'outil : l'API le refuserait.
    assert all(m.content or m.tool_calls for m in last.messages if m.role == "assistant")
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
        # Revue du 27/10/2026 : un contenu d'ailleurs, même sans adresse web.
        ("GitHub (list_issues)", True),
        ("agent recherche", True),
        ("registre MCP", True),
        ("ton écran", True),
        ("bac à sable", False),
    ],
)
def test_what_counts_as_read_from_the_web(source: str | None, web: bool) -> None:
    assert from_web(source) is web


def test_a_command_that_talks_to_the_network_counts_as_the_web() -> None:
    assert from_web("commande", "curl -s https://exemple.com | jq .") is True
    assert from_web("commande", "git clone https://exemple.com/x.git") is True
    assert from_web("commande", "ls -la && git status") is False


def test_a_file_made_by_an_earlier_command_can_be_edited_in_the_same_plan(
    client: TestClient, llm: dict[str, FakeProvider], project: Path
) -> None:
    # Revue du 27/10/2026 : l'aperçu cherchait copie.py avant que la commande la crée.
    (project / "stats.py").write_text(CODE)
    copy = {"commande": "cp stats.py copie.py", "dossier": "~/projet"}
    steps = [
        {"outil": SHELL_TOOL, "arguments": copy, "pourquoi": "copier"},
        {
            "outil": "modifier_fichier",
            "arguments": {**FIX, "chemin": "~/projet/copie.py"},
            "pourquoi": "corriger la copie",
        },
    ]
    llm["deepseek"]._script = scripted(
        [[ToolCall("p", PLAN_TOOL, {"titre": "Copier puis corriger", "etapes": steps})]]
    )
    plan = next(d for e, d in shell_chat(client, "Copie puis corrige.") if e == "plan")
    assert "preview" not in plan["steps"][1]  # le fichier n'existe qu'après l'étape 1
    assert client.post(f"/v1/plans/{plan['id']}/approve", json={}).status_code == 200
    assert wait_plan(client, plan["id"])["status"] == "done"
    assert "if notes else 0" in (project / "copie.py").read_text()


def test_after_two_failures_of_the_same_command_the_relay_model_takes_over(
    client: TestClient, llm: dict[str, FakeProvider], project: Path, services: Services
) -> None:
    del project
    # Profil « Tout Meta » ; ici le relais est DeepSeek Flash (Meta n'a pas de clé en test).
    settings = {"model_profile": "meta", "relay_model": "deepseek/deepseek-flash"}
    assert client.patch("/v1/settings", json=settings).status_code == 200
    llm["deepseek"]._script = shell_script("cat absent.txt > copie.txt")  # échoue : code 1
    events = shell_chat(client, "Copie absent.txt.")
    conversation_id = events[0][1]["conversation_id"]
    routes: list[dict[str, Any]] = []
    for _ in range(2):
        plan = next(d for e, d in events if e == "plan")
        assert client.post(f"/v1/plans/{plan['id']}/approve", json={}).status_code == 200
        assert wait_plan(client, plan["id"])["status"] == "done"
        llm["deepseek"]._script = again_script("cat absent.txt > copie.txt")
        events = shell_chat(
            client, "Continue.", conversation_id=conversation_id, after_plan=[plan["id"]]
        )
        routes.append(next(d for e, d in events if e == "route"))
    # Un échec : Muse Spark (ici le modèle habituel) continue. Deux échecs : le relais.
    assert routes[0]["relay"] is None
    assert routes[1]["model"] == "deepseek-flash"
    assert routes[1]["relay"] == (
        "2 échecs de « cat absent.txt > copie.txt » : relais vers deepseek-flash"
    )
    # Le relais coupé (0) : plus jamais.
    assert client.patch("/v1/settings", json={"relay_after": 0}).status_code == 200
    plan = next(d for e, d in events if e == "plan")
    assert client.post(f"/v1/plans/{plan['id']}/approve", json={}).status_code == 200
    wait_plan(client, plan["id"])
    events = shell_chat(
        client, "Continue.", conversation_id=conversation_id, after_plan=[plan["id"]]
    )
    assert next(d for e, d in events if e == "route")["relay"] is None


def again_script(command: str) -> Script:
    """Le modèle relance la même commande à chaque suite, puis attend ta validation."""

    def script(request: LLMRequest) -> FakeReply:
        if request.tools and request.tools[0].name == TOOL_NAME:
            return FakeReply(tool_calls=[ToolCall("m", TOOL_NAME, {"faits": []})])
        if "titre" in request.messages[0].content:
            return FakeReply(text="Session shell")
        if any(m.role == "tool" for m in request.messages):
            return FakeReply(text="J'attends ta validation.")
        arguments = {"commande": command, "dossier": "~/projet"}
        return FakeReply(text="Je relance.", tool_calls=[ToolCall("c", SHELL_TOOL, arguments)])

    return script


@pytest.mark.parametrize(
    ("text", "claim"),
    [
        ("Voici le plan : on vide le cache npm.", True),
        ("Je te propose un plan de nettoyage.", True),
        ("Ton disque est à 91 %. Tu veux que je te propose un plan de nettoyage sûr ?", False),
        ("Veux-tu que je prépare un plan ?", False),
        ("Ton disque est à 91 %.", False),
    ],
)
def test_an_offer_is_not_an_announced_plan(text: str, claim: bool) -> None:
    # Essai du 28/10/2026 : « tu veux que je te propose un plan ? » relançait le modèle, qui
    # proposait le plan sans attendre la réponse.
    assert claims_missing_plan(text, []) is claim


def test_a_command_you_ran_yourself_returns_as_data_and_goes_to_the_audit_log(
    client: TestClient, llm: dict[str, FakeProvider], project: Path
) -> None:
    del project
    llm["deepseek"]._script = shell_script("ls")
    events = shell_chat(client, "Combien pèse Docker ?")
    conversation_id = events[0][1]["conversation_id"]
    ran = {"command": "sudo du -sh /var/lib/docker", "code": 0, "output": "21G\t/var/lib/docker"}
    shell_chat(client, "Continue.", conversation_id=conversation_id, ran=ran)
    shell = [r for r, _ in llm["deepseek"].requests if "Mode shell" in r.messages[0].content]
    last = next(m.content for m in reversed(shell[-1].messages) if m.role == "user")
    assert "L'utilisateur a lancé lui-même la commande proposée (code de sortie 0)" in last
    assert '<donnees origine="commande lancée par l’utilisateur">' in last
    assert "21G" in last
    audit = client.get("/v1/audit").json()
    mine = [i for i in audit["items"] if i["action"] == "commande.utilisateur"]
    assert [(i["summary"], i["actor"]) for i in mine] == [
        ("Lancée par toi : sudo du -sh /var/lib/docker", "utilisateur")
    ]
    # Hors du mode shell, ou sans conversation : refusé.
    body = {"text": "x", "ran": ran}
    assert client.post("/v1/chat", json=body).status_code != 200
