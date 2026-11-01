"""Les aides de test du chat : lire un flux SSE, scénariser un modèle qui appelle des outils,
attendre la fin d'un plan."""

from __future__ import annotations

import json
import time
from typing import Any

from fastapi.testclient import TestClient

from ojarvis.llm.providers.fake import FakeProvider, FakeReply, Script
from ojarvis.llm.structured import TOOL_NAME
from ojarvis.llm.types import LLMRequest, ToolCall


def read_sse(text: str) -> list[tuple[str, dict[str, Any]]]:
    events = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        events.append((lines["event"], json.loads(lines["data"])))
    return events


def scripted(rounds: list[list[ToolCall]], final: str = "C'est fait.") -> Script:
    """Le modèle appelle des outils tour après tour, puis conclut."""

    def script(request: LLMRequest) -> FakeReply:
        system = request.messages[0].content if request.messages else ""
        if request.tools and request.tools[0].name == TOOL_NAME:
            return FakeReply(tool_calls=[ToolCall("m", TOOL_NAME, {"faits": []})])
        if "titre" in system:
            return FakeReply(text="Conversation outillée")
        if "Tu viens d'exécuter un plan" in system:
            return FakeReply(text="Synthèse : le plan s'est déroulé.")
        done = sum(1 for m in request.messages if m.role == "assistant" and m.tool_calls)
        if done < len(rounds):
            return FakeReply(text="Je regarde.", tool_calls=rounds[done])
        return FakeReply(text=final)

    return script


def wait_plan(client: TestClient, plan_id: str, timeout_s: float = 10.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        plan: dict[str, Any] = client.get(f"/v1/plans/{plan_id}").json()
        if plan["status"] in ("done", "failed"):
            return plan
        time.sleep(0.05)
    raise AssertionError(f"plan toujours {plan['status']}")


def tool_messages(provider: FakeProvider) -> list[str]:
    return [
        m.content for request, _ in provider.requests for m in request.messages if m.role == "tool"
    ]
