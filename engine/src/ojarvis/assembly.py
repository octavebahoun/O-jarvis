"""Assemblage du moteur, morceau par morceau : secrets, actions (journal, outils, plans),
mémoire, routeur des modèles, agents, serveurs MCP, accès à distance. `Services` les
réunit et les démarre (`services.py`).
"""

from __future__ import annotations

import functools
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anyio.to_thread
import httpx

from ojarvis.agent.executor import PlanExecutor
from ojarvis.agent.loop import Agent
from ojarvis.agent.plans import PlanStore
from ojarvis.agents.orchestrator import Orchestrator
from ojarvis.agents.specialists import STARTERS
from ojarvis.anticipation.predictor import Predictor
from ojarvis.anticipation.repetition import RepetitionWatcher
from ojarvis.anticipation.service import AnticipationService
from ojarvis.anticipation.store import SuggestionStore
from ojarvis.audit.journal import AuditJournal
from ojarvis.automations.scheduler import AutomationScheduler
from ojarvis.automations.store import AutomationStore
from ojarvis.browser.proxy import GuardProxy
from ojarvis.browser.service import BrowserAccess
from ojarvis.browser.vault import CookieVault
from ojarvis.chat.service import ChatService
from ojarvis.chrome.bridge import ChromeBridge
from ojarvis.config import EngineConfig
from ojarvis.context.engine import RELEVANCE_FLOOR, ContextEngine
from ojarvis.conversations.store import ConversationStore
from ojarvis.core.clock import local_timezone
from ojarvis.core.errors import UnavailableError
from ojarvis.core.logs import get_logger
from ojarvis.db.engine import Database
from ojarvis.desktop.film import Filmer
from ojarvis.desktop.locate import Locator, ScreenMarks
from ojarvis.desktop.pointer import PortalPointer
from ojarvis.desktop.sam import SamClient
from ojarvis.desktop.screen import capture
from ojarvis.desktop.screencast import ScreenShare, live_or_capture
from ojarvis.events.bus import EventBus
from ojarvis.integrations.google import GoogleAccount, attach_agenda
from ojarvis.integrations.manager import IntegrationManager
from ojarvis.integrations.mcp_registry import McpRegistryService
from ojarvis.llm.catalog import Catalog
from ojarvis.llm.policy import Route
from ojarvis.llm.providers.base import Provider
from ojarvis.llm.providers.registry import OllamaProvider
from ojarvis.llm.router import ModelRouter
from ojarvis.memory.embeddings import Embedder
from ojarvis.memory.graph import GraphStore
from ojarvis.memory.store import MemoryStore
from ojarvis.notifications import Notifier
from ojarvis.permissions.gate import PermissionGate
from ojarvis.sandbox.runner import SandboxRunner
from ojarvis.security.secrets import (
    LOCAL_KEY_NAME,
    KeyringBackend,
    MemoryBackend,
    SecretBackend,
    SecretStore,
)
from ojarvis.settings_store import Preferences, SettingsStore
from ojarvis.shell.machine import MachineCard
from ojarvis.shell.servers import BackgroundSessions
from ojarvis.skills.runner import SkillRunner
from ojarvis.skills.service import SkillService
from ojarvis.skills.store import SkillStore
from ojarvis.telegram.bot import TelegramService
from ojarvis.tools.agents import agent_tools
from ojarvis.tools.automations import automation_tools
from ojarvis.tools.base import ToolContext, ToolRegistry
from ojarvis.tools.catalog import build_registry
from ojarvis.tools.code import ListCapabilities
from ojarvis.tools.desktop import desktop_tools
from ojarvis.tools.impasses import impasse_tools
from ojarvis.tools.integrations import integration_tools
from ojarvis.tools.machine import ReadMachineMap
from ojarvis.tools.mcp import mcp_tools
from ojarvis.tools.page_view import LookAtPage
from ojarvis.tools.screen import ScreenMode
from ojarvis.tools.shell import RunCommand
from ojarvis.tools.skills import skill_tools
from ojarvis.tools.sudo import ProposeAdmin
from ojarvis.tools.tab import tab_tools
from ojarvis.tools.terminal import OpenShell
from ojarvis.tracing.tracer import Tracer
from ojarvis.voice.stt_online import GroqTranscriber
from ojarvis.workflows.engine import WorkflowEngine

log = get_logger(__name__)


class UnavailableBackend:
    """Trousseau absent : le moteur démarre quand même, l'interface affiche l'erreur."""

    name = "indisponible"

    def get(self, key: str) -> str | None:
        del key
        return None

    def set(self, key: str, value: str) -> None:
        del key, value
        raise UnavailableError("Trousseau du système indisponible.", code="keyring_unavailable")

    def delete(self, key: str) -> None:
        self.set(key, "")


async def _open_secrets(
    config: EngineConfig, db: Database, secret_backend: SecretBackend | None
) -> tuple[SecretStore, SecretBackend, str | None, list[str]]:
    """Trousseau du système (ou de test), clé locale, import des clés présentes dans .env."""
    keyring_error: str | None = None
    backend: SecretBackend
    if secret_backend is not None:
        backend = secret_backend
    elif config.dev_mode and os.environ.get("OJARVIS_MEMORY_KEYRING") == "1":
        # Tests de bout en bout : trousseau en mémoire, jamais le vrai, jamais sur disque.
        backend = MemoryBackend()
    else:
        try:
            backend = await anyio.to_thread.run_sync(KeyringBackend)
        except UnavailableError as exc:
            keyring_error = exc.message
            backend = UnavailableBackend()
            log.error("keyring_unavailable")
    secrets = SecretStore(db, backend)
    imported: list[str] = []
    if keyring_error is None:
        await anyio.to_thread.run_sync(secrets.ensure_local_key)
        if config.dotenv_path is not None:
            imported = await anyio.to_thread.run_sync(secrets.import_dotenv, config.dotenv_path)
    await anyio.to_thread.run_sync(secrets.load_into_redactor)
    return secrets, backend, keyring_error, imported


async def _startup_preferences(
    settings: SettingsStore,
    config: EngineConfig,
    providers: dict[str, Provider] | None,
) -> Preferences:
    """Préférences au démarrage ; en e2e, le profil « Auto » des fournisseurs simulés."""
    preferences = await settings.get()
    if (
        providers is None
        and config.dev_mode
        and os.environ.get("OJARVIS_FAKE_LLM") == "1"
        and preferences.model_profile != "auto"
    ):
        preferences, _ = await settings.update({"model_profile": "auto"})
    return preferences


@dataclass
class _Actions:
    """Ce qui permet à Jarvis d'agir et de s'organiser (assemblé à part, pour la lisibilité)."""

    journal: AuditJournal
    integrity_ok: bool
    guard: GuardProxy
    browser: BrowserAccess
    sandbox: SandboxRunner
    notifier: Notifier
    automations: AutomationStore
    workflows: WorkflowEngine
    scheduler: AutomationScheduler
    integrations: IntegrationManager
    tools: ToolRegistry
    skills: SkillService
    gate: PermissionGate
    plans: PlanStore
    executor: PlanExecutor
    marks: ScreenMarks
    share: ScreenShare
    chrome: ChromeBridge


async def _assemble_actions(  # noqa: PLR0917 - un assemblage, pas une logique
    config: EngineConfig,
    db: Database,
    bus: EventBus,
    secrets: SecretStore,
    http: httpx.AsyncClient,
    router: ModelRouter,
    conversations: ConversationStore,
    prefs: Callable[[], Preferences],
    *,
    tracer: Tracer | None = None,
) -> _Actions:
    # Jarvis agit : journal immuable vérifié au démarrage, outils derrière une seule porte.
    journal = AuditJournal(db, bus)
    integrity = await journal.verify()
    user_home = config.user_home or Path.home()
    vault = CookieVault(
        config.paths.data_dir / "navigateur" / "coffre.bin",
        lambda: secrets.get(LOCAL_KEY_NAME),
    )
    # Toute lecture du web (navigateur, pages) passe par ce proxy : adresses publiques.
    guard = GuardProxy(allowed_hosts=config.tool_private_hosts)
    await guard.start()
    browser = BrowserAccess(
        db=db,
        vault=vault,
        journal=journal,
        bus=bus,
        home=user_home,
        allowed_hosts=config.tool_private_hosts,
        proxy=guard,
        visible=lambda: prefs().browser_visible,
    )
    await browser.start()
    sandbox = SandboxRunner()
    # Jarvis s'organise : automatisations, workflows persistés, notifications.
    notifier = Notifier(bus)
    automations = AutomationStore(db, local_timezone)
    workflows = WorkflowEngine(db, bus)
    scheduler = AutomationScheduler(store=automations, workflows=workflows, bus=bus)
    integrations = IntegrationManager(secrets.get)
    servers = BackgroundSessions()  # les serveurs lancés par le mode shell

    def screen_vision() -> ScreenMode:
        # « Tout Meta » n'a pas de modèle local : l'écran est lu par Muse Spark.
        current = prefs()
        return "en_ligne" if current.model_profile == "meta" else current.screen_vision

    # Trouver puis cliquer un élément de l'écran (#48) : SAM 3.1 encadre, Muse Spark choisit,
    # le portail de GNOME clique (avec ton accord).
    desktop = config.paths.state_dir / "desktop"
    marks = ScreenMarks(desktop / "reperes")
    sam = SamClient(lambda: secrets.get("MODEL_API_KEY"), http, tracer)

    async def share_event(event: str, payload: dict[str, Any]) -> None:
        await bus.publish(event, payload, persist=False)

    # L'écran partagé en direct : les outils lisent sa dernière image au lieu d'une capture.
    share = ScreenShare(desktop / "partage-ecran.jeton", notify=share_event)
    screen = live_or_capture(share, capture)
    # Ton onglet Chrome : le panneau de l'extension y fait les actions validées.
    chrome = ChromeBridge()
    tools = build_registry(
        browser.session,
        [
            *automation_tools(automations, scheduler),
            *integration_tools(integrations),
            *desktop_tools(
                router,
                screen_vision,
                locator=Locator(sam, router, marks, screen),
                pointer=PortalPointer(desktop / "controle-a-distance.jeton"),
                grab=screen,
                filmer=Filmer(
                    share,
                    lambda: secrets.get("MODEL_API_KEY"),
                    http,
                    desktop / "travail",
                    tracer=tracer,
                ),
            ),
            # Proposés seulement en mode shell ; journaux et captures dans l'état du moteur.
            RunCommand(user_home, log_dir=config.paths.state_dir / "shell", sessions=servers),
            LookAtPage(
                router,
                screen_vision,
                servers,
                capture_dir=config.paths.state_dir / "shell" / "captures",
            ),
            OpenShell(user_home),  # « passe en mode shell » : ouvre un terminal
            ProposeAdmin(),  # sudo : proposé par Jarvis, lancé par toi
            *tab_tools(chrome),  # proposés seulement dans le panneau de Chrome
        ],
    )
    # Jarvis s'étend : les skills actifs apportent leurs outils, testés en bac à sable.
    skills = SkillService(SkillStore(db), SkillRunner(sandbox), tools, bus)
    for tool in skill_tools(skills):
        tools.register(tool)
    capabilities = tools.get("lister_capacites")
    if isinstance(capabilities, ListCapabilities):
        capabilities.add_section("Skills", skills.capabilities)
    await skills.start()

    def tool_context() -> ToolContext:
        return ToolContext(
            http=http,
            secret=secrets.get,
            home=user_home,
            sandbox=sandbox,
            allowed_private_hosts=config.tool_private_hosts,
            fetch_proxy=f"http://127.0.0.1:{guard.port}",
        )

    gate = PermissionGate(
        tools, journal, tool_context, os_control=lambda: prefs().os_control_enabled
    )
    plans = PlanStore(db, gate)
    executor = PlanExecutor(
        plans=plans,
        gate=gate,
        journal=journal,
        router=router,
        conversations=conversations,
        bus=bus,
    )
    return _Actions(
        journal=journal,
        integrity_ok=integrity.ok,
        guard=guard,
        browser=browser,
        sandbox=sandbox,
        notifier=notifier,
        automations=automations,
        workflows=workflows,
        scheduler=scheduler,
        integrations=integrations,
        tools=tools,
        skills=skills,
        gate=gate,
        plans=plans,
        executor=executor,
        marks=marks,
        share=share,
        chrome=chrome,
    )


def _assemble_anticipation(
    chat: ChatService,
    conversations: ConversationStore,
    graph: GraphStore,
    *,
    db: Database,
    bus: EventBus,
    embedder: Embedder,
) -> AnticipationService:
    """Apprendre de tes demandes répétées, anticiper d'après tes habitudes (lecture seule)."""
    store = SuggestionStore(db, bus)
    return AnticipationService(
        store=store,
        watcher=RepetitionWatcher(
            conversations, store, embedder_id=embedder.model_id, timezone=local_timezone
        ),
        predictor=Predictor(graph, store),
        chat=chat,
        conversations=conversations,
        bus=bus,
    )


async def _assemble_memory(
    db: Database, bus: EventBus, embedder: Embedder
) -> tuple[MemoryStore, ConversationStore, GraphStore, ContextEngine]:
    """Mémoire (faits, conversations, graphe) et moteur de contexte qui y puise."""
    memory = MemoryStore(db, embedder, bus)
    await memory.start()
    conversations = ConversationStore(db, embedder)
    await conversations.start()
    graph = GraphStore(db, bus)
    context = ContextEngine(memory, conversations, embedder_id=embedder.model_id, graph=graph)
    return memory, conversations, graph, context


def _memory_tools(
    acts: _Actions, memory: MemoryStore, embedder: Embedder, home: Path
) -> MachineCard:
    """Les outils qui ont besoin de la mémoire (elle n'existe qu'ici) : les impasses du mode
    shell, et la carte de la machine, relevée en lecture seule."""
    floor = RELEVANCE_FLOOR.get(embedder.model_id, 0.5)
    for tool in impasse_tools(memory, floor=floor):
        acts.tools.register(tool)
    machine = MachineCard(memory, acts.journal, home)
    acts.tools.register(ReadMachineMap(machine))
    return machine


def relay_policy(prefs: Preferences) -> tuple[Route, int] | None:
    """Le relais du profil « Tout Meta » : (modèle de relais, échecs avant de l'appeler)."""
    if prefs.model_profile != "meta" or prefs.relay_after == 0:
        return None
    provider, _, model = prefs.relay_model.partition("/")
    return Route(provider, model, thinking=True), prefs.relay_after


def _build_router(
    providers: dict[str, Provider],
    catalog: Catalog,
    *,
    bus: EventBus,
    tracer: Tracer,
    holder: dict[str, Preferences],
    http: httpx.AsyncClient,
) -> ModelRouter:
    ollama = providers.get("ollama")
    return ModelRouter(
        providers,
        catalog,
        bus=bus,
        tracer=tracer,
        overrides=lambda: holder["value"].routing,
        profile=lambda: holder["value"].model_profile,
        # Avant une demande « locale seulement », au plus une fois par minute.
        refresh_local=(
            functools.partial(ollama.refresh, http) if isinstance(ollama, OllamaProvider) else None
        ),
    )


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
