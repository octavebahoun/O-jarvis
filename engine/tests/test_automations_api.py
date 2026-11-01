"""Automatisations par l'API : CRUD, journal, lancement, consigne, création depuis le chat,
et survie à un redémarrage du moteur."""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import update

from ojarvis.app import create_app
from ojarvis.config import EngineConfig
from ojarvis.core.clock import utc_now
from ojarvis.db.models import Automation
from ojarvis.llm.providers.fake import FakeProvider, FakeReply
from ojarvis.llm.structured import TOOL_NAME
from ojarvis.llm.types import LLMRequest, ToolCall
from ojarvis.memory.embeddings import HashEmbedder
from ojarvis.security.secrets import MemoryBackend
from ojarvis.services import Services
from ojarvis.testing.chat import read_sse
from ojarvis.testing.fixtures import TOKEN

DAILY = {"type": "horaire", "quand": {"type": "chaque_jour", "heure": "08:00"}}
REMINDER = {"type": "rappel", "texte": "Boire un verre d'eau."}


def wait_for(check: Callable[[], Any], timeout_s: float = 10.0) -> Any:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError("délai dépassé")


def test_automations_have_a_full_crud_and_are_journaled(client: TestClient) -> None:
    events = client.get("/v1/automations/events").json()
    assert {"type": "plan.finished", "label": "Quand un plan se termine"} in events

    created = client.post(
        "/v1/automations", json={"name": "Eau", "trigger": DAILY, "action": REMINDER}
    )
    assert created.status_code == 201
    automation = created.json()
    assert automation["when"] == "Tous les jours à 8 h 00"
    assert automation["what"] == "Rappel : « Boire un verre d'eau. »"
    assert automation["enabled"] is True
    assert automation["next_run_at"] is not None
    assert automation["created_by"] == "utilisateur"

    patched = client.patch(
        f"/v1/automations/{automation['id']}",
        json={"enabled": False, "name": "Hydratation"},
    ).json()
    assert patched["enabled"] is False
    assert patched["name"] == "Hydratation"
    weekdays = {
        "type": "horaire",
        "quand": {"type": "chaque_jour", "heure": "07:30", "jours": [0, 1, 2, 3, 4]},
    }
    patched = client.patch(f"/v1/automations/{automation['id']}", json={"trigger": weekdays}).json()
    assert patched["when"] == "Du lundi au vendredi à 7 h 30"

    bad = client.post(
        "/v1/automations",
        json={
            "name": "Mauvaise",
            "trigger": {"type": "horaire", "quand": {"type": "chaque_jour", "heure": "26:00"}},
            "action": REMINDER,
        },
    )
    assert bad.status_code == 422

    assert client.delete(f"/v1/automations/{automation['id']}").status_code == 204
    assert client.get("/v1/automations").json() == []
    assert client.get(f"/v1/automations/{automation['id']}").status_code == 404
    actions = [i["action"] for i in reversed(client.get("/v1/audit").json()["items"])]
    assert actions == [
        "automatisation.creee",
        "automatisation.modifiee",
        "automatisation.modifiee",
        "automatisation.supprimee",
    ]
    levels = [i["level"] for i in reversed(client.get("/v1/audit").json()["items"])]
    assert levels == [2, 2, 2, 3]


def test_a_reminder_runs_now_and_notifies(client: TestClient) -> None:
    automation = client.post(
        "/v1/automations", json={"name": "Eau", "trigger": DAILY, "action": REMINDER}
    ).json()
    before = automation["next_run_at"]
    run = client.post(f"/v1/automations/{automation['id']}/run").json()
    assert run["reason"] == "manuel"
    runs = wait_for(
        lambda: [
            r
            for r in client.get(f"/v1/automations/{automation['id']}/runs").json()
            if r["status"] == "done"
        ]
    )
    assert runs[0]["id"] == run["id"]
    notices = wait_for(
        lambda: client.get("/v1/events", params={"type_prefix": "notification"}).json()
    )
    assert notices[0]["payload"]["title"] == "Eau"
    assert notices[0]["payload"]["body"] == "Boire un verre d'eau."
    after = client.get(f"/v1/automations/{automation['id']}").json()
    assert after["run_count"] == 1
    assert after["next_run_at"] == before  # un lancement à la main ne change pas l'horaire
    assert wait_for(
        lambda: client.get(f"/v1/automations/{automation['id']}").json()["last_status"] == "ok"
    )


def test_an_instruction_is_done_by_jarvis_in_its_own_conversation(
    client: TestClient, llm: dict[str, FakeProvider]
) -> None:
    llm["deepseek"]._script = lambda request: FakeReply(text="J'ai regardé : rien de neuf.")
    action = {"type": "consigne", "texte": "Regarde s'il y a du neuf et résume."}
    automation = client.post(
        "/v1/automations", json={"name": "Veille", "trigger": DAILY, "action": action}
    ).json()
    client.post(f"/v1/automations/{automation['id']}/run")
    done = wait_for(
        lambda: [
            r
            for r in client.get(f"/v1/automations/{automation['id']}/runs").json()
            if r["status"] == "done"
        ]
    )
    assert done[0]["summary"] == "J'ai regardé : rien de neuf."
    conversation_id = client.get(f"/v1/automations/{automation['id']}").json()["conversation_id"]
    assert conversation_id == done[0]["conversation_id"]
    conversation = client.get(f"/v1/conversations/{conversation_id}").json()
    assert conversation["channel"] == "automation"
    assert [m["content"] for m in conversation["messages"]] == [
        "Regarde s'il y a du neuf et résume.",
        "J'ai regardé : rien de neuf.",
    ]
    # Le prompt de l'automatisation dit à Jarvis qu'il travaille seul.
    request = next(r for r, _ in llm["deepseek"].requests if r.tools)
    assert "automatisation que l'utilisateur a créée" in request.messages[0].content


def test_jarvis_creates_an_automation_from_the_chat_after_validation(
    client: TestClient, llm: dict[str, FakeProvider]
) -> None:
    arguments = {
        "nom": "Briefing café",
        "declencheur": {"type": "horaire", "quand": {"type": "chaque_jour", "heure": "08:00"}},
        "action": {"type": "consigne", "texte": "Donne-moi la météo et mon agenda."},
    }

    def script(request: LLMRequest) -> FakeReply:
        if request.tools and request.tools[0].name == TOOL_NAME:
            return FakeReply(tool_calls=[ToolCall("m", TOOL_NAME, {"faits": []})])
        if "titre" in request.messages[0].content:
            return FakeReply(text="Automatisation")
        if any(m.role == "tool" for m in request.messages):
            return FakeReply(text="C'est prêt : valide-la pour l'activer.")
        return FakeReply(tool_calls=[ToolCall("a", "creer_automatisation", arguments)])

    llm["deepseek"]._script = script
    response = client.post(
        "/v1/chat", json={"text": "Tous les matins à 8 h, fais-moi un briefing."}
    )
    plan = next(d for e, d in read_sse(response.text) if e == "plan")
    assert plan["max_level"] == 2
    assert plan["steps"][0]["description"] == (
        "Créer l'automatisation « Briefing café » : Tous les jours à 8 h 00 — "
        "Consigne : « Donne-moi la météo et mon agenda. »"
    )
    assert client.get("/v1/automations").json() == []  # rien avant ta validation
    client.post(f"/v1/plans/{plan['id']}/approve", json={})
    created = wait_for(lambda: client.get("/v1/automations").json())
    assert created[0]["name"] == "Briefing café"
    assert created[0]["created_by"] == "jarvis"


def test_an_automation_survives_a_restart_and_catches_up(
    client: TestClient, services: Services, config: EngineConfig, backend: MemoryBackend
) -> None:
    automation = client.post(
        "/v1/automations", json={"name": "Eau", "trigger": DAILY, "action": REMINDER}
    ).json()
    # L'échéance est passée depuis 10 minutes pendant que le moteur était arrêté.
    late = utc_now() - timedelta(minutes=10)
    services.db.call(
        lambda s: s.execute(
            update(Automation).where(Automation.id == automation["id"]).values(next_run_at=late)
        )
    )
    client.__exit__(None, None, None)  # arrêt du moteur

    app = create_app(
        config,
        token=TOKEN,
        secret_backend=backend,
        http_transport=httpx.MockTransport(lambda r: httpx.Response(599)),
        embedder=HashEmbedder(),
        providers={"deepseek": FakeProvider("deepseek")},
        discover=False,
    )
    with TestClient(app, headers={"Authorization": f"Bearer {TOKEN}"}) as restarted:
        after = wait_for(
            lambda: [a for a in restarted.get("/v1/automations").json() if a["run_count"] == 1]
        )
        assert after[0]["id"] == automation["id"]
        assert after[0]["next_run_at"] == automation["next_run_at"]  # prochain 8 h, inchangé
        runs = wait_for(
            lambda: [
                r
                for r in restarted.get(f"/v1/automations/{automation['id']}/runs").json()
                if r["status"] == "done"
            ]
        )
        assert runs[0]["reason"] == "horaire"
