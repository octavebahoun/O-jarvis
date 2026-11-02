"""Skills : brouillon testé en bac à sable, activation après double confirmation, usage."""

from __future__ import annotations

import functools
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from ojarvis.core.errors import ConflictError
from ojarvis.llm.providers.fake import FakeProvider, FakeReply
from ojarvis.llm.structured import TOOL_NAME
from ojarvis.llm.types import LLMRequest, ToolCall
from ojarvis.sandbox.runner import SandboxRunner
from ojarvis.services import Services
from ojarvis.skills.models import SkillDefinition
from ojarvis.skills.runner import SkillCallError, SkillRunner, TestCase, TestReport
from ojarvis.skills.service import SkillService
from ojarvis.skills.store import SkillStore
from ojarvis.testing.chat import wait_plan
from ojarvis.tools.base import InvalidToolArgumentsError, ToolRegistry
from tests.test_agent_chat import chat

DAYS_CODE = '''
from datetime import date


def jours_entre(debut: str, fin: str) -> int:
    """Nombre de jours entre deux dates AAAA-MM-JJ."""
    return (date.fromisoformat(fin) - date.fromisoformat(debut)).days
'''

DAYS_TESTS = """
def test_un_jour():
    assert jours_entre("2026-01-01", "2026-01-02") == 1


def test_une_annee():
    assert jours_entre("2026-01-01", "2027-01-01") == 365
"""

TOOL = "jours_entre_dates__jours_entre"


def days_skill(**over: Any) -> dict[str, Any]:
    return {
        "nom": "jours-entre-dates",
        "version": "1.0.0",
        "description": "Compte les jours entre deux dates.",
        "instructions": "Pour toute question de durée entre deux dates.",
        "outils": [
            {
                "nom": "jours_entre",
                "description": "Jours entre deux dates AAAA-MM-JJ.",
                "parametres": {
                    "type": "object",
                    "properties": {"debut": {"type": "string"}, "fin": {"type": "string"}},
                    "required": ["debut", "fin"],
                    "additionalProperties": False,
                },
            }
        ],
        "code": DAYS_CODE,
        "tests": DAYS_TESTS,
        **over,
    }


def test_a_skill_definition_is_checked() -> None:
    skill = SkillDefinition.model_validate(days_skill())
    assert skill.digest() == SkillDefinition.model_validate(days_skill()).digest()
    assert skill.digest() != SkillDefinition.model_validate(days_skill(version="1.0.1")).digest()
    bad_values: list[dict[str, Any]] = [
        {"nom": "Jours_Entre"},  # minuscules, chiffres et tirets seulement
        {"nom": "a--b"},
        {"version": "1.0"},
        {"tests": "assert True  # aucune fonction test_"},
        {"outils": []},
        {"outils": [days_skill()["outils"][0], days_skill()["outils"][0]]},  # même nom
        {"outils": [{**days_skill()["outils"][0], "parametres": {"type": "string"}}]},
        {"outils": [{**days_skill()["outils"][0], "nom": "__init__"}]},
        {
            "outils": [
                {
                    **days_skill()["outils"][0],
                    "parametres": {
                        "type": "object",
                        "properties": {f"p{i}": {"type": "string"} for i in range(400)},
                    },
                }
            ]
        },
    ]
    for bad in bad_values:
        with pytest.raises(ValidationError):
            SkillDefinition.model_validate(days_skill(**bad))


# ---------------------------------------------------------------------- vrai bac à sable


@pytest.fixture
async def sandbox() -> SandboxRunner:
    runner = SandboxRunner(timeout_s=20.0)
    if await runner.backend() is None:
        pytest.skip("aucun bac à sable ici")
    return runner


async def test_tests_and_calls_run_in_the_sandbox(sandbox: SandboxRunner) -> None:
    runner = SkillRunner(sandbox)
    report = await runner.test(SkillDefinition.model_validate(days_skill()))
    assert report.ok, report
    assert [c.name for c in report.cases] == ["test_un_jour", "test_une_annee"]
    assert report.summary() == "2/2 tests réussis"
    skill = SkillDefinition.model_validate(days_skill())
    assert (
        await runner.call(skill, "jours_entre", {"debut": "2026-01-01", "fin": "2026-10-15"}) == 287
    )
    with pytest.raises(SkillCallError, match="ValueError"):
        await runner.call(skill, "jours_entre", {"debut": "hier", "fin": "2026-10-15"})


async def test_failures_are_reported_precisely(sandbox: SandboxRunner) -> None:
    runner = SkillRunner(sandbox)
    wrong = days_skill(tests=DAYS_TESTS.replace("== 365", "== 366"))
    report = await runner.test(SkillDefinition.model_validate(wrong))
    assert not report.ok
    failed = [c for c in report.cases if not c.ok]
    assert [c.name for c in failed] == ["test_une_annee"]
    assert "366" in failed[0].detail  # la ligne de l'assert qui a échoué
    broken = await runner.test(SkillDefinition.model_validate(days_skill(code="def jours_entre(:")))
    assert broken.error is not None
    assert broken.error.startswith("Le code ne se charge pas")
    missing = await runner.test(
        SkillDefinition.model_validate(days_skill(code="def autre():\n    return 1"))
    )
    assert missing.error == "Fonction absente du code : jours_entre"


async def test_a_skill_has_no_network_and_no_files(sandbox: SandboxRunner) -> None:
    code = """
import socket


def sortir() -> str:
    try:
        socket.create_connection(("1.1.1.1", 53), timeout=2)
        return "réseau"
    except OSError:
        return "isolé"


def lire() -> str:
    import os
    return "visible" if os.path.exists(os.path.expanduser("~/.ssh")) else "invisible"
"""
    skill = SkillDefinition.model_validate(
        days_skill(
            code=code,
            tests="def test_rien():\n    assert True",
            outils=[
                {"nom": "sortir", "description": "Tente de sortir."},
                {"nom": "lire", "description": "Tente de lire."},
            ],
        )
    )
    runner = SkillRunner(sandbox)
    assert await runner.call(skill, "sortir", {}) == "isolé"
    assert await runner.call(skill, "lire", {}) == "invisible"


async def test_an_endless_skill_is_stopped(
    sandbox: SandboxRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("ojarvis.skills.runner.CALL_TIMEOUT_S", 2.0)
    skill = SkillDefinition.model_validate(
        days_skill(code="def jours_entre(debut, fin):\n    while True:\n        pass")
    )
    with pytest.raises(SkillCallError, match="temps d'exécution dépassé"):
        await SkillRunner(sandbox).call(skill, "jours_entre", {"debut": "a", "fin": "b"})


# ---------------------------------------------------------------------- registre et parcours


class FakeRunner:
    """Rapport et résultat fixés : le parcours se teste sans bac à sable."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    async def test(self, skill: SkillDefinition) -> TestReport:
        ok = "== 366" not in skill.tests
        return TestReport(ok=ok, cases=[TestCase("test_une_annee", ok, "" if ok else "== 366")])

    async def call(self, skill: SkillDefinition, tool: str, args: dict[str, Any]) -> Any:
        self.calls.append((skill.version, tool, args))
        return 287


@pytest.fixture
def fake_runner(services: Services, monkeypatch: pytest.MonkeyPatch) -> FakeRunner:
    runner = FakeRunner()
    monkeypatch.setattr(services.skills, "_runner", runner)
    return runner


def test_jarvis_creates_a_skill_asks_then_uses_it(
    client: TestClient,
    services: Services,
    llm: dict[str, FakeProvider],
    fake_runner: FakeRunner,
) -> None:
    """Le critère de la phase 6 : il crée un skill, le teste, demande, puis l'utilise."""

    def script(request: LLMRequest) -> FakeReply:  # noqa: PLR0911 - un modèle simulé
        system = request.messages[0].content if request.messages else ""
        if request.tools and request.tools[0].name == TOOL_NAME:
            return FakeReply(tool_calls=[ToolCall("m", TOOL_NAME, {"faits": []})])
        if "titre" in system:
            return FakeReply(text="Skill")
        if "Tu viens d'exécuter un plan" in system:
            return FakeReply(text="Le skill est actif.")
        last_user = next(m.content for m in reversed(request.messages) if m.role == "user")
        done = sum(1 for m in request.messages if m.role == "assistant" and m.tool_calls)
        if "Crée-toi" in last_user:
            if done == 0:
                return FakeReply(tool_calls=[ToolCall("c", "creer_skill", days_skill())])
            return FakeReply(text="Ses tests réussissent : je l'active dès que tu confirmes.")
        if done == 0:
            dates = {"debut": "2026-01-01", "fin": "2026-10-15"}
            return FakeReply(tool_calls=[ToolCall("j", TOOL, dates)])
        return FakeReply(text="Il y a 287 jours.")

    llm["deepseek"]._script = script
    events = chat(client, "Crée-toi un skill qui compte les jours entre deux dates.")
    assert services.skills.known("jours-entre-dates", "1.0.0").status == "pret"  # type: ignore[union-attr]
    assert TOOL not in services.tools  # pas encore : il attend ta confirmation
    plan = next(d for e, d in events if e == "plan")
    assert plan["max_level"] == 3
    step = plan["steps"][0]["description"]
    assert "outils : jours_entre" in step
    assert "ni Internet, ni tes fichiers" in step
    assert "1/1 tests réussis" in step
    once = client.post(f"/v1/plans/{plan['id']}/approve", json={})
    assert once.json()["error"]["code"] == "double_confirmation_required"
    client.post(f"/v1/plans/{plan['id']}/approve", json={"double_confirm": True})
    assert wait_plan(client, plan["id"])["status"] == "done"
    assert TOOL in services.tools

    events = chat(client, "Combien de jours entre le 1er janvier et le 15 octobre 2026 ?")
    assert fake_runner.calls == [
        ("1.0.0", "jours_entre", {"debut": "2026-01-01", "fin": "2026-10-15"})
    ]
    said = "".join(d["text"] for e, d in events if e == "delta")
    assert "287" in said
    journal = [i["summary"] for i in client.get("/v1/audit").json()["items"]]
    assert any(
        s.startswith("Fait : Activer le skill « jours-entre-dates » v1.0.0") for s in journal
    )


def test_versions_replace_each_other_and_survive_a_restart(
    client: TestClient, services: Services, fake_runner: FakeRunner
) -> None:
    assert client.portal is not None
    client.portal.call(_versions, services, fake_runner)


async def _versions(services: Services, fake_runner: FakeRunner) -> None:
    skills = services.skills
    first = await skills.propose(SkillDefinition.model_validate(days_skill()), created_by="jarvis")
    await skills.activate(first.id)
    with pytest.raises(ConflictError, match="choisis une version plus haute"):
        await skills.propose(SkillDefinition.model_validate(days_skill()), created_by="jarvis")
    second = await skills.propose(
        SkillDefinition.model_validate(days_skill(version="1.1.0")), created_by="jarvis"
    )
    await skills.activate(second.id)
    assert skills.known("jours-entre-dates", "1.0.0").status == "remplace"  # type: ignore[union-attr]
    await services.gate.execute(TOOL, {"debut": "2026-01-01", "fin": "2026-01-02"})
    assert fake_runner.calls[-1][0] == "1.1.0"  # la nouvelle version répond
    with pytest.raises(InvalidToolArgumentsError, match="debut"):
        services.gate.check(TOOL, {"fin": "2026-01-02"})

    failing = await skills.propose(
        SkillDefinition.model_validate(
            days_skill(version="2.0.0", tests=DAYS_TESTS.replace("== 365", "== 366"))
        ),
        created_by="jarvis",
    )
    assert failing.status == "echec"
    with pytest.raises(ConflictError, match="tests"):
        await skills.activate(failing.id)

    # Au redémarrage, le skill actif retrouve son outil.
    registry = ToolRegistry()
    again = SkillService(SkillStore(services.db), fake_runner, registry, services.bus)  # type: ignore[arg-type]
    await again.start()
    assert TOOL in registry
    with pytest.raises(ConflictError, match="Désactive"):
        await skills.delete(second.id)
    await skills.disable(second.id)
    assert TOOL not in services.tools


def test_the_skills_screen_api(
    client: TestClient, services: Services, fake_runner: FakeRunner
) -> None:
    del fake_runner
    assert client.portal is not None
    propose = functools.partial(
        services.skills.propose,
        SkillDefinition.model_validate(days_skill()),
        created_by="jarvis",
    )
    record = client.portal.call(propose)
    listed = client.get("/v1/skills").json()
    assert [(s["name"], s["status"]) for s in listed] == [("jours-entre-dates", "pret")]
    assert listed[0]["tools"][0]["name"] == TOOL
    assert "code" not in listed[0]
    detail = client.get(f"/v1/skills/{record.id}").json()
    assert "def jours_entre" in detail["code"]
    assert detail["report"]["summary"] == "1/1 tests réussis"

    refused = client.post(f"/v1/skills/{record.id}/activate", json={})
    assert refused.status_code == 409
    assert TOOL not in services.tools
    active = client.post(f"/v1/skills/{record.id}/activate", json={"double_confirm": True}).json()
    assert active["status"] == "actif"
    tools = {t["name"] for t in client.get("/v1/tools").json()}
    assert TOOL in tools
    assert (
        client.delete(f"/v1/skills/{record.id}").status_code == 409
    )  # actif, et sans confirmation
    assert client.post(f"/v1/skills/{record.id}/disable").json()["status"] == "desactive"
    assert client.delete(f"/v1/skills/{record.id}?double_confirm=true").status_code == 204
    assert client.get("/v1/skills").json() == []
    journal = [(i["summary"], i["level"]) for i in client.get("/v1/audit").json()["items"]]
    assert journal == [
        ("Skill supprimé : « jours-entre-dates » v1.0.0", 3),
        ("Skill désactivé : « jours-entre-dates » v1.0.0", 2),
        ("Skill activé : « jours-entre-dates » v1.0.0", 3),
    ]


def test_capabilities_list_the_skills(
    client: TestClient, services: Services, fake_runner: FakeRunner
) -> None:
    del fake_runner
    assert client.portal is not None
    propose = functools.partial(
        services.skills.propose,
        SkillDefinition.model_validate(days_skill()),
        created_by="jarvis",
    )
    client.portal.call(propose)
    out = client.portal.call(services.gate.execute, "lister_capacites", {})
    assert "Skills :" in out.text
    assert "- jours-entre-dates v1.0.0 [pret] : Compte les jours entre deux dates." in out.text
    assert "Serveurs MCP" not in out.text or "aucun" in out.text
