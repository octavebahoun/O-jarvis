"""Les commandes du mode shell restent dans le mode shell."""

from __future__ import annotations

from fastapi.testclient import TestClient
from ojarvis.agent.plans import PLAN_TOOL
from ojarvis.llm.providers.fake import FakeProvider
from ojarvis.llm.types import ToolCall
from ojarvis.testing.chat import read_sse, scripted


def test_outside_shell_mode_a_command_is_neither_offered_nor_run(
    client: TestClient, llm: dict[str, FakeProvider]
) -> None:
    command = {"commande": "ls", "dossier": "~"}
    step = {"outil": "executer_commande", "arguments": command, "pourquoi": "x"}
    llm["deepseek"]._script = scripted(
        [
            [ToolCall("c", "executer_commande", command)],
            [ToolCall("p", PLAN_TOOL, {"titre": "Lister", "etapes": [step]})],
        ]
    )
    response = client.post("/v1/chat", json={"text": "Liste mes fichiers avec ls."})
    events = read_sse(response.text)
    offered = {t.name for request, _ in llm["deepseek"].requests for t in request.tools or []}
    assert "executer_commande" not in offered
    failed = [d for e, d in events if e == "tool" and d["status"] == "failed"]
    assert len(failed) == 2  # l'appel direct, puis le plan
    assert all("mode shell" in d["error"] for d in failed)
    assert not [e for e, _ in events if e == "plan"]
