"""L'assemblage de ce qui étend Jarvis : ses agents, les serveurs MCP du registre, et ce qui
le joint de loin (Google, Telegram)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import anyio.to_thread
import httpx

from ojarvis.agent.loop import Agent
from ojarvis.agents.orchestrator import Orchestrator
from ojarvis.agents.specialists import STARTERS
from ojarvis.chat.service import ChatService
from ojarvis.config import EngineConfig
from ojarvis.db.engine import Database
from ojarvis.events.bus import EventBus
from ojarvis.integrations.google import GoogleAccount, attach_agenda
from ojarvis.integrations.mcp_registry import McpRegistryService
from ojarvis.llm.router import ModelRouter
from ojarvis.security.secrets import (
    SecretBackend,
    SecretStore,
)
from ojarvis.telegram.bot import TelegramService
from ojarvis.tools.agents import agent_tools
from ojarvis.tools.code import ListCapabilities
from ojarvis.tools.mcp import mcp_tools
from ojarvis.tracing.tracer import Tracer
from ojarvis.voice.stt_online import GroqTranscriber

if TYPE_CHECKING:
    from ojarvis.assembly import _Actions


async def _assemble_agents(
    acts: _Actions,
    core: Agent,
    *,
    router: ModelRouter,
    db: Database,
    bus: EventBus,
    tracer: Tracer,
) -> Orchestrator:
    """Les agents (dev, recherche, coach) passent par le même cœur : même porte, mêmes plans."""
    orchestrator = Orchestrator(
        {a.name: a(router=router, core=core) for a in STARTERS},
        db=db,
        bus=bus,
        tracer=tracer,
        notifier=acts.notifier,
    )
    for tool in agent_tools(orchestrator):
        acts.tools.register(tool)
    await orchestrator.recover()
    return orchestrator


async def _assemble_mcp(
    acts: _Actions,
    *,
    http: httpx.AsyncClient,
    db: Database,
    backend: SecretBackend,
    config: EngineConfig,
    bus: EventBus,
) -> McpRegistryService:
    """Jarvis s'étend aussi par les serveurs MCP du registre officiel (installés isolés)."""
    mcp = McpRegistryService(
        http=http,
        db=db,
        manager=acts.integrations,
        backend=backend,
        data_dir=config.paths.data_dir,
        bus=bus,
    )
    for tool in mcp_tools(mcp):
        acts.tools.register(tool)
    capabilities = acts.tools.get("lister_capacites")
    if isinstance(capabilities, ListCapabilities):
        capabilities.add_section("Serveurs MCP installés", mcp.capabilities)
    await mcp.start()
    return mcp


async def _assemble_remote(  # noqa: PLR0917 - un assemblage, pas une logique
    acts: _Actions,
    http: httpx.AsyncClient,
    secrets: SecretStore,
    backend: SecretBackend,
    bus: EventBus,
    chat: ChatService,
) -> tuple[GoogleAccount, TelegramService]:
    """Ce qui relie Jarvis à tes comptes : Google (agenda) et Telegram (téléphone)."""
    google = GoogleAccount(http=http, secret=secrets.get, backend=backend, bus=bus)
    await anyio.to_thread.run_sync(google.load_into_redactor)

    async def google_connected() -> None:
        await attach_agenda(google, acts.integrations, http)

    google.on_connected = google_connected
    telegram = TelegramService(
        http=http,
        secret=secrets.get,
        chat=chat,
        plans=acts.plans,
        executor=acts.executor,
        notifier=acts.notifier,
        bus=bus,
        transcriber=GroqTranscriber(lambda: secrets.get("GROQ_API_KEY"), http),
    )
    return google, telegram
