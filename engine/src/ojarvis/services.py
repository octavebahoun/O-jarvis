"""Assemblage des services du moteur, démarrés et arrêtés avec l'API."""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Coroutine
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import anyio.to_thread
import httpx

from ojarvis import __version__
from ojarvis.agent.executor import PlanExecutor
from ojarvis.agent.loop import Agent
from ojarvis.agent.plans import PlanStore
from ojarvis.agent.review import CodeReviewer
from ojarvis.agents.orchestrator import Orchestrator
from ojarvis.anticipation.service import AnticipationService
from ojarvis.assembly import (
    _assemble_actions,
    _assemble_anticipation,
    _assemble_memory,
    _build_router,
    _memory_tools,
    _open_secrets,
    _startup_preferences,
    relay_policy,
)
from ojarvis.assembly_extensions import _assemble_agents, _assemble_mcp, _assemble_remote
from ojarvis.audit.journal import AuditJournal
from ojarvis.automations.scheduler import AutomationScheduler
from ojarvis.automations.steps import AutomationSteps
from ojarvis.automations.store import AutomationStore
from ojarvis.briefing.service import BriefingService
from ojarvis.browser.proxy import GuardProxy
from ojarvis.browser.service import BrowserAccess
from ojarvis.chat.service import ChatService
from ojarvis.chrome.bridge import ChromeBridge
from ojarvis.config import EngineConfig
from ojarvis.context.engine import ContextEngine
from ojarvis.conversations.store import ConversationStore
from ojarvis.core.clock import utc_now
from ojarvis.core.logs import get_logger
from ojarvis.db.engine import Database
from ojarvis.db.migrate import upgrade_to_head
from ojarvis.desktop.locate import ScreenMarks
from ojarvis.desktop.screencast import ScreenShare
from ojarvis.events.bus import Event, EventBus
from ojarvis.events.store import EventStore
from ojarvis.integrations.google import GoogleAccount, attach_agenda
from ojarvis.integrations.manager import IntegrationManager
from ojarvis.integrations.mcp_registry import McpRegistryService
from ojarvis.llm.catalog import Catalog
from ojarvis.llm.providers.base import Provider
from ojarvis.llm.providers.fake import FakeProvider, e2e_script
from ojarvis.llm.providers.registry import OllamaProvider, build_providers, discover_openrouter_free
from ojarvis.llm.router import ModelRouter
from ojarvis.memory.embeddings import E5Embedder, Embedder, HashEmbedder
from ojarvis.memory.graph import GraphStore
from ojarvis.memory.store import MemoryStore
from ojarvis.notifications import Notifier
from ojarvis.permissions.gate import PermissionGate
from ojarvis.sandbox.runner import SandboxRunner
from ojarvis.security.secrets import (
    SecretBackend,
    SecretStore,
)
from ojarvis.settings_store import Preferences, SettingsStore
from ojarvis.shell.machine import MachineCard
from ojarvis.skills.service import SkillService
from ojarvis.telegram.bot import TelegramService
from ojarvis.tools.base import ToolRegistry
from ojarvis.tracing.tracer import TraceQueries, Tracer
from ojarvis.voice.service import VoiceService
from ojarvis.voice.simulated import simulated_voice
from ojarvis.workflows.engine import WorkflowEngine

log = get_logger(__name__)

#: Modèle local de repli (voir la politique du routeur) et fréquence maximale de préchargement.
LOCAL_MODEL = "ministral-3:3b"
LOCAL_PRELOAD_EVERY_S = 300.0


@dataclass
class Services:
    config: EngineConfig
    db: Database
    bus: EventBus
    events: EventStore
    secrets: SecretStore
    settings: SettingsStore
    tracer: Tracer
    traces: TraceQueries
    http: httpx.AsyncClient
    started_at: datetime
    keyring_error: str | None
    imported_secrets: list[str]
    preferences: Preferences
    catalog: Catalog
    providers: dict[str, Provider]
    router: ModelRouter
    embedder: Embedder
    memory: MemoryStore
    conversations: ConversationStore
    context: ContextEngine
    chat: ChatService
    voice: VoiceService
    journal: AuditJournal
    tools: ToolRegistry
    gate: PermissionGate
    plans: PlanStore
    executor: PlanExecutor
    browser: BrowserAccess
    sandbox: SandboxRunner
    guard: GuardProxy
    notifier: Notifier
    automations: AutomationStore
    workflows: WorkflowEngine
    scheduler: AutomationScheduler
    telegram: TelegramService
    integrations: IntegrationManager
    google: GoogleAccount
    skills: SkillService
    mcp: McpRegistryService
    orchestrator: Orchestrator
    graph: GraphStore
    anticipation: AnticipationService
    machine: MachineCard
    #: Les éléments trouvés à l'écran (#48) et leurs captures annotées.
    marks: ScreenMarks
    #: L'écran partagé en direct (portail ScreenCast).
    share: ScreenShare
    #: Les actions dans ton onglet Chrome, faites par le panneau de l'extension.
    chrome: ChromeBridge
    briefing: BriefingService = field(init=False)
    background: set[asyncio.Task[None]] = field(default_factory=set)

    @classmethod
    async def start(
        cls,
        config: EngineConfig,
        *,
        secret_backend: SecretBackend | None = None,
        http_transport: httpx.AsyncBaseTransport | None = None,
        embedder: Embedder | None = None,
        providers: dict[str, Provider] | None = None,
        discover: bool = True,
    ) -> Services:
        config.paths.ensure()
        await anyio.to_thread.run_sync(upgrade_to_head, config.paths.db_path)
        db = Database(config.paths.db_path)
        events = EventStore(db)
        bus = EventBus(events.persist)

        secrets, backend, keyring_error, imported = await _open_secrets(config, db, secret_backend)

        settings = SettingsStore(db)
        preferences = await _startup_preferences(settings, config, providers)
        tracer = Tracer(db, bus)
        http = httpx.AsyncClient(timeout=httpx.Timeout(10.0), transport=http_transport)
        catalog = Catalog()
        if providers is None and config.dev_mode and os.environ.get("OJARVIS_FAKE_LLM") == "1":
            # Tests de bout en bout : réponses prévisibles, aucun appel à un vrai modèle.
            providers = {"deepseek": FakeProvider("deepseek", script=e2e_script, delay_s=0.05)}
        providers = providers if providers is not None else build_providers(secrets)
        holder: dict[str, Preferences] = {"value": preferences}
        router = _build_router(providers, catalog, bus=bus, tracer=tracer, holder=holder, http=http)
        # « Tout Meta » n'appelle aucun modèle d'un autre éditeur : la mémoire y cherche par
        # mots hachés plutôt qu'avec e5. Le changement de profil vaut au prochain démarrage.
        embedder = embedder or (
            HashEmbedder()
            if preferences.model_profile == "meta"
            else E5Embedder(config.paths.cache_dir / "models" / "multilingual-e5-small")
        )
        memory, conversations, graph, context = await _assemble_memory(db, bus, embedder)

        acts = await _assemble_actions(
            config,
            db,
            bus,
            secrets,
            http,
            router,
            conversations,
            lambda: holder["value"],
            tracer=tracer,
        )
        machine = _memory_tools(acts, memory, embedder, config.user_home or Path.home())
        core = Agent(
            gate=acts.gate,
            plans=acts.plans,
            bus=bus,
            executor=acts.executor,
            reviewer=CodeReviewer(router),
        )
        chat = ChatService(
            conversations=conversations,
            memory=memory,
            context=context,
            router=router,
            tracer=tracer,
            bus=bus,
            learn=preferences.learn_from_conversations,
            agent=core,
            graph=graph,
            relay=lambda: relay_policy(holder["value"]),
            machine=machine,
        )
        orchestrator = await _assemble_agents(
            acts, core, router=router, db=db, bus=bus, tracer=tracer
        )
        google, telegram = await _assemble_remote(acts, http, secrets, backend, bus, chat)
        mcp = await _assemble_mcp(acts, http=http, db=db, backend=backend, config=config, bus=bus)
        anticipation = _assemble_anticipation(
            chat, conversations, graph, db=db, bus=bus, embedder=embedder
        )
        anticipation.start(periodic=discover)

        # Audio réel seulement pour un vrai lancement : jamais dans les tests ni en CI.
        # Tests de bout en bout : audio simulé (pas de micro, de haut-parleur ni de modèle).
        simulated = config.dev_mode and os.environ.get("OJARVIS_FAKE_AUDIO") == "1"
        audio = simulated or (
            discover
            and os.environ.get("OJARVIS_SKIP_WARMUP") != "1"
            and os.environ.get("OJARVIS_NO_AUDIO") != "1"
        )
        voice = VoiceService(
            models_dir=config.paths.cache_dir / "models",
            http=http,
            bus=bus,
            tracer=tracer,
            chat=chat,
            secrets=secrets,
            preferences=lambda: holder["value"],
            audio=audio,
            **(simulated_voice() if simulated else {}),
        )

        services = cls(
            config=config,
            db=db,
            bus=bus,
            events=events,
            secrets=secrets,
            settings=settings,
            tracer=tracer,
            traces=TraceQueries(db),
            http=http,
            started_at=utc_now(),
            keyring_error=keyring_error,
            imported_secrets=imported,
            preferences=preferences,
            catalog=catalog,
            providers=providers,
            router=router,
            embedder=embedder,
            memory=memory,
            conversations=conversations,
            context=context,
            chat=chat,
            voice=voice,
            journal=acts.journal,
            tools=acts.tools,
            gate=acts.gate,
            plans=acts.plans,
            executor=acts.executor,
            browser=acts.browser,
            sandbox=acts.sandbox,
            guard=acts.guard,
            notifier=acts.notifier,
            automations=acts.automations,
            workflows=acts.workflows,
            scheduler=acts.scheduler,
            telegram=telegram,
            integrations=acts.integrations,
            google=google,
            skills=acts.skills,
            mcp=mcp,
            orchestrator=orchestrator,
            graph=graph,
            anticipation=anticipation,
            machine=machine,
            marks=acts.marks,
            share=acts.share,
            chrome=acts.chrome,
        )
        services._holder = holder
        await acts.executor.recover()
        acts.workflows.handlers.update(
            AutomationSteps(store=acts.automations, chat=chat, notifier=acts.notifier).handlers()
        )
        services.briefing = BriefingService(
            settings=settings,
            workflows=acts.workflows,
            router=router,
            conversations=conversations,
            automations=acts.automations,
            integrations=acts.integrations,
            google=google,
            voice=voice,
            notifier=acts.notifier,
            bus=bus,
            http=http,
        )
        acts.workflows.handlers.update(services.briefing.handlers())
        await acts.workflows.start()
        await acts.scheduler.start()
        # Telegram : seulement pour un vrai lancement (un banc d'essai ou un test ne doit pas
        # répondre à tes messages à la place de l'app).
        if discover or os.environ.get("OJARVIS_TELEGRAM") == "1":
            await services.telegram.start()
            # Les serveurs MCP configurés démarrent en arrière-plan (Docker, réseau).
            services.spawn(acts.integrations.start_configured())
            if google.connected:
                services.spawn(services._attach_agenda())
        # OJARVIS_SKIP_WARMUP=1 : aucun accès réseau ni téléchargement au démarrage (tests).
        if discover and os.environ.get("OJARVIS_SKIP_WARMUP") != "1":
            services.spawn(services._warm_up())
            bus.subscribe("llm.provider_degraded", services._preload_local_model)
        services.spawn(voice.start())
        await bus.publish(
            "engine.started",
            {
                "version": __version__,
                "dev_mode": config.dev_mode,
                "keyring": backend.name,
                "imported_secrets": imported,
                "audit_ok": acts.integrity_ok,
            },
        )
        log.info("engine_started", version=__version__, keyring=backend.name)
        return services

    _holder: dict[str, Preferences] = field(default_factory=dict)
    _last_local_preload: float = -LOCAL_PRELOAD_EVERY_S

    async def _preload_local_model(self, event: Event) -> None:
        """Un fournisseur en ligne tombe : on charge le modèle local en mémoire, en avance.

        Son premier chargement prend de 13 à 50 s sur cette machine ; s'il est déjà en
        mémoire quand le repli arrive, la réponse part tout de suite.
        """
        ollama = self.providers.get("ollama")
        if event.payload.get("provider") == "ollama" or not isinstance(ollama, OllamaProvider):
            return
        now = time.monotonic()
        if now - self._last_local_preload < LOCAL_PRELOAD_EVERY_S:
            return
        self._last_local_preload = now
        await ollama.refresh(self.http)
        if LOCAL_MODEL in ollama.models:
            log.info("local_model_preload", model=LOCAL_MODEL)
            await ollama.warm_up(self.http, LOCAL_MODEL)

    async def _attach_agenda(self) -> None:
        try:
            await attach_agenda(self.google, self.integrations, self.http)
        except Exception:
            log.warning("agenda_attach_failed")

    def spawn(self, coroutine: Coroutine[Any, Any, None]) -> None:
        """Tâche de fond suivie (annulée proprement à l'arrêt)."""
        task = asyncio.create_task(coroutine)
        self.background.add(task)
        task.add_done_callback(self.background.discard)

    def set_preferences(self, preferences: Preferences) -> None:
        previous = self.preferences
        self.preferences = preferences
        self._holder["value"] = preferences
        self.chat.learn = preferences.learn_from_conversations
        # La voix suit aussi le profil : couper « Tout Meta » cesse tout de suite d'envoyer ta
        # voix à Meta.
        voice_keys = [
            k
            for k in Preferences.model_fields
            if k.startswith(("voice_", "wake_")) or k == "model_profile"
        ]
        if any(getattr(previous, k) != getattr(preferences, k) for k in voice_keys):
            self.spawn(self.voice.apply(preferences))

    async def _warm_up(self) -> None:
        """Au démarrage, sans bloquer : modèle d'embeddings, modèles gratuits, Ollama."""
        ollama = self.providers.get("ollama")
        if isinstance(ollama, OllamaProvider):
            await ollama.refresh(self.http, force=True)
        if self.providers.get("openrouter") is not None:
            count = await discover_openrouter_free(self.catalog, self.http)
            log.info("openrouter_free_models", count=count)
        if not await self._start_memory():
            return
        await self.bus.publish("engine.ready", {"embeddings": self.embedder.status}, persist=False)

    async def _start_memory(self) -> bool:
        """Charge le modèle d'embeddings (téléchargé s'il manque) puis démarre la mémoire."""
        if not isinstance(self.embedder, E5Embedder) or self.embedder.status == "prêt":
            return True
        try:
            await self.embedder.ensure(self.http)
        except Exception:
            log.exception("embedding_model_unavailable")
            return False
        await self.memory.start()
        await self.conversations.start()
        return True

    @property
    def models_dir(self) -> Path:
        return self.config.paths.cache_dir / "models"

    def model_states(self) -> dict[str, str]:
        """État vivant de chaque modèle local (téléchargement en cours, erreur…)."""
        states = dict(self.voice.status.models)
        states["memoire"] = self.embedder.status
        return states

    def retry_local_models(self) -> None:
        """Relance, en tâche de fond, ce qui manque : la voix si elle est activée, la mémoire."""
        if self.preferences.voice_enabled and self.voice.status.state == "error":
            self.spawn(self.voice.apply(self.preferences))
        if self.embedder.status != "prêt":
            self.spawn(self._start_memory_and_announce())

    async def _start_memory_and_announce(self) -> None:
        if await self._start_memory():
            await self.bus.publish(
                "engine.ready", {"embeddings": self.embedder.status}, persist=False
            )

    async def stop(self) -> None:
        await self.bus.publish("engine.stopping", {})
        await self.share.stop()
        await self.voice.close()
        await self.chat.close()
        await self.anticipation.close()
        await self.orchestrator.close()
        await self.telegram.close()
        await self.integrations.close()
        await self.google.close()
        await self.scheduler.close()
        await self.workflows.close()
        await self.executor.close()
        await self.browser.close()
        await self.guard.close()
        for task in list(self.background):
            task.cancel()
        await asyncio.gather(*list(self.background), return_exceptions=True)
        await self.bus.drain()
        await self.http.aclose()
        self.db.dispose()
        log.info("engine_stopped")
