"""Registre MCP : fiches lues prudemment, serveurs lancés isolés, installés après double
confirmation, clés au trousseau seulement."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, cast
from urllib.parse import unquote

import httpx
import pytest
from fastapi.testclient import TestClient
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from ojarvis.core.errors import InvalidInputError
from ojarvis.integrations import registry
from ojarvis.integrations.mcp_registry import McpRegistryService, environment, local_id
from ojarvis.integrations.registry import InstallOption, Runtimes, parse_entry
from ojarvis.llm.providers.fake import FakeProvider, FakeReply
from ojarvis.llm.structured import TOOL_NAME
from ojarvis.llm.types import LLMRequest, ToolCall
from ojarvis.security.redaction import REDACTOR
from ojarvis.security.secrets import MemoryBackend
from ojarvis.services import Services
from ojarvis.testing.chat import wait_plan
from tests.test_agent_chat import chat

WIKI = "io.github.cyanheads/wikipedia-mcp-server"
# Forme réelle d'une fiche du registre (octobre 2026), raccourcie.
WIKI_ENTRY: dict[str, Any] = {
    "server": {
        "name": WIKI,
        "description": "Search Wikipedia, read summaries and full text.",
        "repository": {
            "url": "https://github.com/cyanheads/wikipedia-mcp-server",
            "source": "github",
        },
        "version": "0.2.6",
        "packages": [
            {
                "registryType": "npm",
                "identifier": "@cyanheads/wikipedia-mcp-server",
                "version": "0.2.6",
                "runtimeHint": "npx",
                "transport": {"type": "stdio"},
                "environmentVariables": [
                    {"name": "MCP_LOG_LEVEL", "description": "Log level.", "default": "info"}
                ],
            },
            {
                "registryType": "npm",
                "identifier": "@cyanheads/wikipedia-mcp-server",
                "version": "0.2.6",
                "transport": {"type": "streamable-http", "url": "http://localhost:3010/mcp"},
                "environmentVariables": [{"name": "MCP_TRANSPORT_TYPE", "value": "http"}],
            },
        ],
        "remotes": [{"type": "streamable-http", "url": "https://wikipedia.example.org/mcp"}],
    },
    "_meta": {},
}
KEYED = "io.example/notes"
KEYED_ENTRY: dict[str, Any] = {
    "server": {
        "name": KEYED,
        "title": "Notes",
        "description": "Des notes, avec une clé.",
        "version": "1.4.0",
        "packages": [
            {
                "registryType": "pypi",
                "identifier": "notes-mcp",
                "version": "1.4.0",
                "transport": {"type": "stdio"},
                "environmentVariables": [
                    {"name": "NOTES_API_KEY", "isRequired": True, "isSecret": True},
                    {"name": "NOTES_REGION", "description": "Région.", "default": "eu"},
                ],
            }
        ],
    }
}


def entry(raw: dict[str, Any], **server: Any) -> dict[str, Any]:
    data = copy.deepcopy(raw)
    data["server"].update(server)
    return data


def test_registry_entries_are_read_carefully() -> None:
    wiki = parse_entry(WIKI_ENTRY)
    assert wiki is not None
    assert wiki.title == "wikipedia-mcp-server"  # pas de titre : la fin du nom
    kinds = [(o.kind, o.problem) for o in wiki.options]
    assert kinds[0] == ("npm", None)
    assert kinds[1][1] is not None
    assert "serveur web" in kinds[1][1]  # transport http refusé
    assert kinds[2] == ("remote", None)
    local = Runtimes(bwrap="/usr/bin/bwrap", npx="/usr/bin/npx")
    best = wiki.best(local)
    assert best is not None
    assert (best.kind, best.label) == ("npm", "paquet npm @cyanheads/wikipedia-mcp-server@0.2.6")
    assert [v.name for v in best.asked()] == ["MCP_LOG_LEVEL"]
    # Sans rien pour isoler un paquet, il ne reste que le service en ligne.
    online = wiki.best(Runtimes())
    assert online is not None
    assert online.kind == "remote"
    assert online.where == "en ligne, chez wikipedia.example.org"

    def problem(package: dict[str, Any]) -> str | None:
        parsed = parse_entry(entry(WIKI_ENTRY, packages=[package], remotes=[]))
        assert parsed is not None
        return parsed.options[0].problem

    npm = WIKI_ENTRY["server"]["packages"][0]
    assert "latest" in (problem({**npm, "version": "latest"}) or "")
    assert problem({**npm, "identifier": "-rf"}) == "nom de paquet npm inattendu"
    image = {"registryType": "oci", "identifier": "docker.io/x/y:latest"}
    assert problem(image) == "image Docker sans version précise"
    assert problem({**image, "identifier": "docker.io/x/y:1.2.3"}) is None
    required_arg = {**npm, "packageArguments": [{"type": "positional", "isRequired": True}]}
    assert "pas encore pris en charge" in (problem(required_arg) or "")
    headers = parse_entry(
        entry(
            WIKI_ENTRY,
            packages=[],
            remotes=[
                {
                    "type": "streamable-http",
                    "url": "https://a.b/mcp",
                    "headers": [{"name": "Authorization"}],
                }
            ],
        )
    )
    assert headers is not None
    assert "en-tête" in (headers.options[0].problem or "")
    plain_http = parse_entry(
        entry(
            WIKI_ENTRY, packages=[], remotes=[{"type": "streamable-http", "url": "http://a.b/mcp"}]
        )
    )
    assert plain_http is not None
    assert "https" in (plain_http.options[0].problem or "")
    assert parse_entry({"server": "texte"}) is None


def test_runtimes_decide_what_can_run() -> None:
    npm = InstallOption("npm", "a", "1.0.0")
    pypi = InstallOption("pypi", "b", "1.0.0")
    oci = InstallOption("oci", "docker.io/c:1", "1")
    bwrap_only = Runtimes(bwrap="/usr/bin/bwrap", npx="/usr/bin/npx")
    assert bwrap_only.can_run(npm)
    assert not bwrap_only.can_run(pypi)  # pas d'uvx
    assert not bwrap_only.can_run(oci)  # une image demande Docker
    assert Runtimes(docker="/usr/bin/docker").can_run(oci)
    assert Runtimes(npx="/usr/bin/npx").can_run(npm) is False  # rien pour l'isoler
    assert "ni Docker ni bubblewrap" in Runtimes().missing()


def test_servers_are_launched_isolated_without_secrets_in_arguments(tmp_path: Path) -> None:
    npm = InstallOption("npm", "@cyanheads/wikipedia-mcp-server", "0.2.6")
    secret = "sk-" + "x" * 30
    env = {"NOTES_API_KEY": secret}
    target = registry.docker_target(npm, "wiki", env, docker="/usr/bin/docker")
    assert secret not in " ".join(target.args)
    assert target.env["NOTES_API_KEY"] == secret
    assert target.args[target.args.index("NOTES_API_KEY") - 1 :][:2] == ["-e", "NOTES_API_KEY"]
    for flag in (
        "--cap-drop",
        "ALL",
        "no-new-privileges",
        "--memory",
        "ojarvis-mcp-wiki:/home/node",
    ):
        assert flag in target.args
    assert target.args[-3:] == ["npx", "-y", "@cyanheads/wikipedia-mcp-server@0.2.6"]
    pypi = registry.docker_target(
        InstallOption("pypi", "notes-mcp", "1.4.0"), "notes", {}, docker="d"
    )
    assert pypi.args[-3:] == [registry.UV_IMAGE, "uvx", "notes-mcp@1.4.0"]

    runtimes = Runtimes(bwrap="/usr/bin/bwrap", npx=str(tmp_path / "node" / "bin" / "npx"))
    home = tmp_path / "mcp" / "wiki"
    jail = registry.bubblewrap_target(npm, home, env, runtimes=runtimes)
    assert secret not in " ".join(jail.args)
    assert jail.env["NOTES_API_KEY"] == secret
    assert ["--bind", str(home), "/home/mcp"] == jail.args[jail.args.index(str(home)) - 1 :][:3]
    assert ["--ro-bind", str(tmp_path / "node"), "/opt/runtime"] == jail.args[
        jail.args.index("/opt/runtime") - 2 :
    ][:3]
    assert str(Path.home()) not in jail.args  # ton dossier personnel n'est jamais monté
    assert "--unshare-all" in jail.args
    assert "--share-net" in jail.args
    assert jail.args[-3:] == ["/opt/runtime/bin/npx", "-y", "@cyanheads/wikipedia-mcp-server@0.2.6"]


def test_settings_follow_what_the_publisher_declared() -> None:
    option = parse_entry(KEYED_ENTRY).options[0]  # type: ignore[union-attr]
    with pytest.raises(InvalidInputError, match="La clé NOTES_API_KEY est obligatoire"):
        environment(option, {}, {})
    with pytest.raises(InvalidInputError, match="à saisir à part"):
        environment(option, {"NOTES_API_KEY": "k"}, {})
    with pytest.raises(InvalidInputError, match="inconnu"):
        environment(option, {"AUTRE": "x"}, {"NOTES_API_KEY": "k"})
    assert environment(option, {}, {"NOTES_API_KEY": "k"}) == {
        "NOTES_API_KEY": "k",
        "NOTES_REGION": "eu",
    }
    assert local_id(WIKI, set()) == "wikipedia-mcp-server"
    assert local_id(WIKI, {"wikipedia-mcp-server"}) == "wikipedia-mcp-server-2"
    assert local_id("io.github.someone/GitHub", set()) == "github-2"  # nom réservé


# ---------------------------------------------------------------------- parcours complet


def fake_wikipedia() -> MCPServer:
    server = MCPServer("faux-wikipedia")

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    def wikipedia_get_summary(title: str) -> str:
        """Résumé d'un article."""
        return f"{title} : ville du Bénin, capitale économique."

    @server.tool()
    def wikipedia_edit(title: str, text: str) -> str:
        """Modifie un article."""
        return "modifié"

    return server


@pytest.fixture
def registry_api(upstream: dict[str, Any]) -> list[str]:
    seen: list[str] = []

    def route(request: httpx.Request) -> httpx.Response:
        path = unquote(request.url.path)
        seen.append(path)
        by_name = {WIKI: WIKI_ENTRY, KEYED: KEYED_ENTRY}
        if path == "/v0/servers":
            query = request.url.params.get("search", "")
            found = [e for name, e in by_name.items() if query in name]
            return httpx.Response(200, json={"servers": found, "metadata": {"count": len(found)}})
        for name, raw in by_name.items():
            if path == f"/v0/servers/{name}/versions/{raw['server']['version']}":
                return httpx.Response(200, json=raw)
        return httpx.Response(404, json={"detail": "not found"})

    upstream["registry.modelcontextprotocol.io"] = route
    return seen


@pytest.fixture
def isolated(
    services: Services, monkeypatch: pytest.MonkeyPatch
) -> list[tuple[str, str, dict[str, str]]]:
    """Le serveur « lancé » est un faux serveur en mémoire ; on garde ce qu'on lui a passé."""
    launched: list[tuple[str, str, dict[str, str]]] = []

    def launcher(option: InstallOption, server_id: str, env: dict[str, str]) -> MCPServer:
        launched.append((option.label, server_id, env))
        return fake_wikipedia()

    async def runtimes() -> Runtimes:
        return Runtimes(docker="/usr/bin/docker")

    monkeypatch.setattr(services.mcp, "_launcher", launcher)
    monkeypatch.setattr(services.mcp, "_runtimes", runtimes)
    return launched


def test_search_try_install_use_then_remove(
    client: TestClient, services: Services, registry_api: list[str], isolated: list[Any]
) -> None:
    found = client.get("/v1/mcp/registry", params={"q": "wikipedia"}).json()
    assert [(e["name"], e["option"]["kind"]) for e in found] == [(WIKI, "npm")]
    assert found[0]["option"]["settings"][0]["name"] == "MCP_LOG_LEVEL"

    tried = client.post("/v1/mcp/probe", json={"name": WIKI, "version": "0.2.6"}).json()
    assert tried["ok"] is True, tried
    assert [(t["name"], t["level"]) for t in tried["tools"]] == [
        ("wikipedia_edit", 2),
        ("wikipedia_get_summary", 1),
    ]
    assert not client.get("/v1/mcp/servers").json()  # essayé n'est pas installé

    body = {"name": WIKI, "version": "0.2.6"}
    refused = client.post("/v1/mcp/servers", json=body)
    assert refused.status_code == 409
    installed = client.post("/v1/mcp/servers", json={**body, "double_confirm": True}).json()
    assert installed["id"] == "wikipedia-mcp-server"
    assert installed["label"] == "paquet npm @cyanheads/wikipedia-mcp-server@0.2.6"
    assert isolated[-1][2] == {"MCP_LOG_LEVEL": "info"}  # valeur par défaut de l'éditeur

    assert client.portal is not None
    listed = client.portal.call(
        services.gate.execute, "integrations_outils", {"serveur": "wikipedia-mcp-server"}
    )
    assert listed.external  # descriptions écrites par un tiers : une donnée
    read = client.portal.call(
        services.gate.execute,
        "integration_appeler",
        {
            "serveur": "wikipedia-mcp-server",
            "outil": "wikipedia_get_summary",
            "arguments": {"title": "Cotonou"},
        },
    )
    assert "ville du Bénin" in read.text
    _, _, level = services.gate.check(
        "integration_appeler",
        {
            "serveur": "wikipedia-mcp-server",
            "outil": "wikipedia_edit",
            "arguments": {"title": "a", "text": "b"},
        },
    )
    assert level == 2  # modifier un article attend ta validation

    off = client.patch("/v1/mcp/servers/wikipedia-mcp-server", json={"enabled": False}).json()
    assert off["status"] == "désactivé"
    assert not services.integrations.known("wikipedia-mcp-server")
    assert client.delete("/v1/mcp/servers/wikipedia-mcp-server").status_code == 409
    assert (
        client.delete("/v1/mcp/servers/wikipedia-mcp-server?double_confirm=true").status_code == 204
    )
    assert client.get("/v1/mcp/servers").json() == []
    journal = [(i["summary"], i["level"]) for i in client.get("/v1/audit").json()["items"]]
    assert journal == [
        ("Serveur MCP retiré : « wikipedia-mcp-server » v0.2.6", 3),
        ("Serveur MCP désactivé : « wikipedia-mcp-server » v0.2.6", 2),
        ("Serveur MCP installé : « wikipedia-mcp-server » v0.2.6", 3),
    ]


def test_keys_go_to_the_keyring_and_never_come_back(
    client: TestClient,
    services: Services,
    backend: MemoryBackend,
    registry_api: list[str],
    isolated: list[Any],
) -> None:
    key = "nk-" + "z" * 32
    body = {
        "name": KEYED,
        "version": "1.4.0",
        "variables": {"NOTES_REGION": "af"},
        "secrets": {"NOTES_API_KEY": key},
        "double_confirm": True,
    }
    installed = client.post("/v1/mcp/servers", json=body)
    assert installed.status_code == 201
    assert key not in installed.text
    assert installed.json()["secret_names"] == ["NOTES_API_KEY"]
    assert backend.get("OJARVIS_MCP_NOTES_NOTES_API_KEY") == key
    assert isolated[-1][2] == {"NOTES_API_KEY": key, "NOTES_REGION": "af"}
    assert REDACTOR.redact(f"clé {key}") == "clé ***"
    assert key not in client.get("/v1/mcp/servers").text
    assert key not in json.dumps(client.get("/v1/audit").json())

    # Au redémarrage, le serveur revient avec sa clé lue au trousseau.
    async def restart() -> list[str]:
        again = McpRegistryService(
            http=services.http,
            db=services.db,
            manager=services.integrations,
            backend=backend,
            data_dir=services.config.paths.data_dir,
            bus=services.bus,
            runtimes=services.mcp._runtimes,
            launcher=services.mcp._launcher,
        )
        await services.integrations.remove_custom("notes")
        await again.start()
        await services.integrations.ensure("notes")
        return [s.id for s in again.installed()]

    assert client.portal is not None
    assert client.portal.call(restart) == ["notes"]
    assert isolated[-1][2]["NOTES_API_KEY"] == key
    assert client.delete("/v1/mcp/servers/notes?double_confirm=true").status_code == 204
    assert backend.get("OJARVIS_MCP_NOTES_NOTES_API_KEY") is None


def test_jarvis_installs_a_server_only_after_double_confirmation(
    client: TestClient,
    services: Services,
    llm: dict[str, FakeProvider],
    registry_api: list[str],
    isolated: list[Any],
) -> None:
    def script(request: LLMRequest) -> FakeReply:  # noqa: PLR0911 - un modèle simulé
        system = request.messages[0].content if request.messages else ""
        if request.tools and request.tools[0].name == TOOL_NAME:
            return FakeReply(tool_calls=[ToolCall("m", TOOL_NAME, {"faits": []})])
        if "titre" in system:
            return FakeReply(text="MCP")
        if "Tu viens d'exécuter un plan" in system:
            return FakeReply(text="Installé.")
        last = next(m.content for m in reversed(request.messages) if m.role == "user")
        done = sum(1 for m in request.messages if m.role == "assistant" and m.tool_calls)
        if "Installe" in last:
            if done == 0:
                return FakeReply(
                    tool_calls=[ToolCall("s", "mcp_chercher", {"requete": "wikipedia"})]
                )
            if done == 1:
                args = {"nom": WIKI, "version": "0.2.6"}
                return FakeReply(tool_calls=[ToolCall("i", "mcp_installer", args)])
            return FakeReply(text="Je l'installe dès que tu confirmes.")
        if done == 0:
            call = {
                "serveur": "wikipedia-mcp-server",
                "outil": "wikipedia_get_summary",
                "arguments": {"title": "Cotonou"},
            }
            return FakeReply(tool_calls=[ToolCall("w", "integration_appeler", call)])
        return FakeReply(text="Cotonou est la capitale économique du Bénin.")

    llm["deepseek"]._script = script
    events = chat(client, "Installe un serveur MCP pour Wikipédia.")
    plan = next(d for e, d in events if e == "plan")
    assert plan["max_level"] == 3
    step = plan["steps"][0]["description"]
    assert "paquet npm @cyanheads/wikipedia-mcp-server@0.2.6" in step
    assert "essayé isolé d'abord" in step
    assert not isolated  # rien n'est lancé avant ta décision
    client.post(f"/v1/plans/{plan['id']}/approve", json={"double_confirm": True})
    assert wait_plan(client, plan["id"])["status"] == "done"
    events = chat(client, "Que dit Wikipédia de Cotonou ?")
    used = [d for e, d in events if e == "tool" and d["name"] == "integration_appeler"]
    assert [d["status"] for d in used] == ["running", "done"]  # une lecture : sans attendre
    said = "".join(d["text"] for e, d in events if e == "delta")
    assert "capitale économique" in said


def test_a_server_needing_a_key_is_installed_by_you_not_by_jarvis(
    client: TestClient, services: Services, registry_api: list[str], isolated: list[Any]
) -> None:
    assert client.portal is not None
    tool = services.tools.get("mcp_installer")
    args = tool.parse({"nom": KEYED, "version": "1.4.0"})
    with pytest.raises(InvalidInputError, match="il la saisit lui-même"):
        client.portal.call(tool.run, args, cast(Any, None))
    assert not isolated  # rien n'a été lancé
