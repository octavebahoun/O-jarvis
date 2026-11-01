"""Intégrations MCP : connexion, niveaux tirés des annotations, appels depuis le chat."""

from __future__ import annotations

import time
from typing import Any

import pytest
from fastapi.testclient import TestClient
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from ojarvis.integrations.connection import McpCallError
from ojarvis.integrations.manager import IntegrationManager
from ojarvis.llm.providers.fake import FakeProvider, FakeReply
from ojarvis.llm.structured import TOOL_NAME
from ojarvis.llm.types import LLMRequest, ToolCall
from ojarvis.services import Services
from ojarvis.testing.chat import read_sse
from ojarvis.tools.base import InvalidToolArgumentsError, Level
from ojarvis.tools.integrations import CallIntegration, ListIntegrationTools


def fake_github() -> MCPServer:
    server = MCPServer("faux-github")
    created: list[dict[str, Any]] = []

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    def list_issues(owner: str, repo: str) -> str:
        """Liste les issues ouvertes d'un dépôt."""
        return f"#1 Bug d'affichage ({owner}/{repo})\n#2 Ajouter le mode sombre"

    @server.tool()
    def create_issue(owner: str, repo: str, title: str) -> str:
        """Crée une issue."""
        created.append({"owner": owner, "repo": repo, "title": title})
        return f"Issue créée : {title}"

    @server.tool(annotations=ToolAnnotations(destructive_hint=True))
    def delete_repository(owner: str, repo: str) -> str:
        """Supprime un dépôt."""
        return "supprimé"

    server.created = created  # type: ignore[attr-defined]
    return server


async def test_tools_are_listed_with_levels_from_their_annotations() -> None:
    server = fake_github()
    manager = IntegrationManager(lambda _: None, targets={"github": lambda: server})
    try:
        tools = await manager.ensure("github")
        assert tools["list_issues"].level == Level.LIRE
        assert tools["create_issue"].level == Level.AGIR
        assert tools["delete_repository"].level == Level.DETRUIRE
        text, is_error = await manager.call("github", "list_issues", {"owner": "o", "repo": "r"})
        assert not is_error
        assert "#1 Bug d'affichage (o/r)" in text
        with pytest.raises(McpCallError, match="pas d'outil"):
            await manager.call("github", "format_disk", {})
        status = {s["name"]: s for s in manager.status()}
        assert status["github"]["status"] == "prêt"
        assert status["github"]["tools"] == 3
        assert status["notion"]["configured"] is False
    finally:
        await manager.close()


async def test_meta_tools_validate_arguments_and_compute_levels() -> None:
    server = fake_github()
    manager = IntegrationManager(lambda _: None, targets={"github": lambda: server})
    try:
        listing = ListIntegrationTools(manager)
        out = await listing.run(listing.parse({"serveur": "github"}), None)  # type: ignore[arg-type]
        assert "create_issue [niveau 2]" in out.text
        assert "owner*: string" in out.text
        caller = CallIntegration(manager)
        read = caller.parse(
            {"serveur": "github", "outil": "list_issues", "arguments": {"owner": "o", "repo": "r"}}
        )
        assert caller.level_for(read) == Level.LIRE
        write = caller.parse(
            {
                "serveur": "github",
                "outil": "create_issue",
                "arguments": {"owner": "o", "repo": "r", "title": "t"},
            }
        )
        assert caller.level_for(write) == Level.AGIR
        assert caller.describe(write).startswith('GitHub : create_issue {"owner": "o"')
        with pytest.raises(InvalidToolArgumentsError, match="title"):
            caller.parse(
                {
                    "serveur": "github",
                    "outil": "create_issue",
                    "arguments": {"owner": "o", "repo": "r"},
                }
            )
        # Serveur prêt : un nom inventé est refusé, avec les vrais noms pour se reprendre.
        with pytest.raises(InvalidToolArgumentsError, match="list_issues") as refused:
            caller.parse({"serveur": "github", "outil": "get_repository", "arguments": {}})
        assert "pas d'outil « get_repository »" in str(refused.value)
        # Outil inconnu tant que le serveur n'a pas répondu : traité comme une action.
        unknown = caller.parse({"serveur": "notion", "outil": "search", "arguments": {}})
        assert caller.level_for(unknown) == Level.AGIR
    finally:
        await manager.close()


async def test_unconfigured_integrations_say_so() -> None:
    manager = IntegrationManager(lambda _: None)
    with pytest.raises(McpCallError, match="n'est pas configuré"):
        await manager.ensure("notion")
    assert [s["configured"] for s in manager.status()] == [False, False, False]


def test_jarvis_reads_github_freely_but_proposes_a_plan_to_write(
    client: TestClient, services: Services, llm: dict[str, FakeProvider]
) -> None:
    server = fake_github()
    services.integrations.set_target("github", lambda: server)
    calls = [
        [ToolCall("t", "integrations_outils", {"serveur": "github"})],
        [
            ToolCall(
                "r",
                "integration_appeler",
                {
                    "serveur": "github",
                    "outil": "list_issues",
                    "arguments": {"owner": "o", "repo": "r"},
                },
            )
        ],
        [
            ToolCall(
                "w",
                "integration_appeler",
                {
                    "serveur": "github",
                    "outil": "create_issue",
                    "arguments": {"owner": "o", "repo": "r", "title": "Mode sombre"},
                },
            )
        ],
    ]

    def script(request: LLMRequest) -> FakeReply:
        if request.tools and request.tools[0].name == TOOL_NAME:
            return FakeReply(tool_calls=[ToolCall("m", TOOL_NAME, {"faits": []})])
        if "titre" in request.messages[0].content:
            return FakeReply(text="GitHub")
        if "Tu viens d'exécuter un plan" in request.messages[0].content:
            return FakeReply(text="Issue créée.")
        done = sum(1 for m in request.messages if m.role == "assistant" and m.tool_calls)
        if done < len(calls):
            return FakeReply(tool_calls=calls[done])
        return FakeReply(text="Deux issues ouvertes ; j'ai préparé la nouvelle.")

    llm["deepseek"]._script = script
    events = read_sse(client.post("/v1/chat", json={"text": "Quoi de neuf sur o/r ?"}).text)
    done = [d for e, d in events if e == "tool" and d["status"] == "done"]
    assert [d["name"] for d in done] == ["integrations_outils", "integration_appeler"]
    plan = next(d for e, d in events if e == "plan")
    assert plan["max_level"] == 2
    assert server.created == []  # type: ignore[attr-defined]  # rien avant ta validation
    framed = [
        m.content for r, _ in llm["deepseek"].requests for m in r.messages if m.role == "tool"
    ]
    assert any(c.startswith('<donnees origine="GitHub (list_issues)">') for c in framed)

    client.post(f"/v1/plans/{plan['id']}/approve", json={})
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not server.created:  # type: ignore[attr-defined]
        time.sleep(0.05)
    assert server.created == [{"owner": "o", "repo": "r", "title": "Mode sombre"}]  # type: ignore[attr-defined]
    status = {s["name"]: s for s in client.get("/v1/integrations").json()}
    assert status["github"]["status"] == "prêt"
