"""L'extension O-jarvis Shell, branchée ici en un seul point si elle est installée.

O-jarvis Shell (https://github.com/octavebahoun/o-jarvis-shell) a été construit pendant le
hackathon Meta, dans son propre dépôt : le mode shell, Jarvis dans Chrome, les clics et le
partage d'écran, les voix et la transcription de Meta, le relecteur de code. C'est le paquet
`ojarvis_shell` ; son module d'entrée, `ojarvis_shell.extension`, reçoit ici ce que le moteur
lui prête.

O-jarvis marche sans lui : ces fonctions sont alors absentes, rien d'autre ne change.
"""

from __future__ import annotations

import importlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    import httpx
    from fastapi import APIRouter

    from ojarvis.audit.journal import AuditJournal
    from ojarvis.config import EngineConfig
    from ojarvis.events.bus import EventBus
    from ojarvis.llm.router import ModelRouter
    from ojarvis.memory.embeddings import Embedder
    from ojarvis.memory.store import MemoryStore
    from ojarvis.security.secrets import SecretStore
    from ojarvis.settings_store import Preferences
    from ojarvis.tools.base import ToolRegistry
    from ojarvis.tools.screen import ScreenMode
    from ojarvis.tracing.tracer import Tracer

#: Le module d'entrée de l'extension.
ENTRY = "ojarvis_shell.extension"


class Review(Protocol):
    """L'avis du relecteur sur un diff, affiché avec le plan et rendu à l'auteur."""

    def as_dict(self) -> dict[str, Any]: ...

    def for_author(self) -> str: ...


class Reviewer(Protocol):
    async def review(self, request: str, previews: list[str]) -> Review | None: ...


class MachineMap(Protocol):
    """La carte de la machine, relevée au premier tour du mode shell."""

    async def ensure(self) -> bool: ...


@dataclass
class Lent:
    """Ce que le moteur prête à l'extension pour s'assembler."""

    config: EngineConfig
    bus: EventBus
    secrets: SecretStore
    http: httpx.AsyncClient
    router: ModelRouter
    journal: AuditJournal
    memory: MemoryStore
    embedder: Embedder
    tools: ToolRegistry
    prefs: Callable[[], Preferences]
    #: Qui lit l'écran : un modèle local, ou en ligne (« Tout Meta » : Muse Spark).
    screen_mode: Callable[[], ScreenMode]
    capture: Callable[[], Awaitable[bytes]]
    home: Path
    tracer: Tracer | None = None


async def _nothing() -> None:
    return None


@dataclass
class Added:
    """Ce que l'extension apporte au moteur, une fois assemblée."""

    #: L'extension Chrome est branchée : ses outils d'onglet sont proposés.
    chrome_connected: Callable[[], bool] = lambda: False
    reviewer: Reviewer | None = None
    machine: MachineMap | None = None
    #: Ses routes d'API (le pont Chrome, le partage d'écran…).
    routers: list[APIRouter] = field(default_factory=list)
    #: Ses propres objets (pont Chrome, écran partagé, repères), que ses routes retrouvent.
    state: Any = None
    #: À l'arrêt du moteur (le partage d'écran, s'il est en cours).
    stop: Callable[[], Awaitable[None]] = _nothing


@cache
def load() -> ModuleType | None:
    """Le module d'entrée de l'extension, ou None si elle n'est pas installée."""
    try:
        return importlib.import_module(ENTRY)
    except ModuleNotFoundError as exc:
        if exc.name not in (ENTRY, ENTRY.partition(".")[0]):
            raise  # installée mais cassée : l'erreur doit se voir
        return None


async def attach(lent: Lent) -> Added | None:
    """Assemble l'extension (outils, pont Chrome, relecteur…) avec ce que le moteur prête."""
    module = load()
    if module is None:
        return None
    added: Added = await module.attach(lent)
    return added


def routers() -> list[APIRouter]:
    """Les routes d'API de l'extension, montées par l'app à côté des siennes."""
    module = load()
    return [] if module is None else list(module.routers())


def talks_to_network(command: str) -> bool:
    """La commande du mode shell lit le réseau (sa sortie vient alors du web) ; sans
    l'extension, il n'y a pas de mode shell, donc pas de commande."""
    module = load()
    return module is not None and bool(module.talks_to_network(command))


def command(name: str) -> Callable[[list[str]], int] | None:
    """Une commande du moteur empaqueté apportée par l'extension (« shell », « chrome-host »)."""
    module = load()
    if module is None:
        return None
    found: Callable[[list[str]], int] | None = module.commands().get(name)
    return found
