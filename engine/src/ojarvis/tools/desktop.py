"""Outils du contrôle du PC : applications, écran, gestion des fichiers.

Tous désactivés par défaut : tu les actives dans Réglages → Contrôle du PC. Ouvrir ou
fermer une application, déplacer ou copier un fichier, créer un dossier, cliquer à l'écran :
niveau 2 (ta validation). Lister, regarder l'écran et y trouver un élément : niveau 1. Ce
que montre l'écran est une donnée, jamais une instruction.
"""

from __future__ import annotations

import shutil
import time
from collections.abc import Awaitable, Callable
from typing import Any, ClassVar
from urllib.parse import urlsplit

import anyio
from pydantic import Field

from ojarvis.core.errors import InvalidInputError, NotFoundError
from ojarvis.desktop import apps as applications
from ojarvis.desktop.screen import capture
from ojarvis.llm.router import ModelRouter
from ojarvis.tools.base import Level, Tool, ToolArgs, ToolContext, ToolOutput
from ojarvis.tools.guards import resolve_user_path, shown
from ojarvis.tools.screen import LookAtScreen, ScreenMode

INDEX_TTL_S = 300.0


class AppCatalog:
    """Index des applications installées, gardé 5 minutes (lu de façon synchrone)."""

    def __init__(
        self, loader: Callable[[], dict[str, applications.AppEntry]] = applications.index
    ) -> None:
        self._loader = loader
        self._apps: dict[str, applications.AppEntry] = {}
        self._at = 0.0

    def entries(self) -> dict[str, applications.AppEntry]:
        if not self._apps or time.monotonic() - self._at > INDEX_TTL_S:
            self._apps = self._loader()
            self._at = time.monotonic()
        return self._apps

    def find(self, name: str) -> applications.AppEntry:
        return applications.find(self.entries(), name)


class FilterArgs(ToolArgs):
    filtre: str = Field(default="", max_length=80, description="Mot à chercher (vide : tout).")


class ListApps(Tool[FilterArgs]):
    name: ClassVar[str] = "lister_applications"
    description: ClassVar[str] = "Liste les applications installées (nom et identifiant)."
    Args: ClassVar[type[ToolArgs]] = FilterArgs
    level: ClassVar[Level] = Level.LIRE
    os_control: ClassVar[bool] = True

    def __init__(self, catalog: AppCatalog) -> None:
        self._catalog = catalog

    def describe(self, args: FilterArgs) -> str:
        return f"Lister les applications{f' ({args.filtre})' if args.filtre else ''}"

    async def run(self, args: FilterArgs, ctx: ToolContext) -> ToolOutput:
        del ctx
        entries = await anyio.to_thread.run_sync(self._catalog.entries)
        wanted = args.filtre.casefold()
        names = sorted(
            e.label for e in entries.values() if not wanted or wanted in e.label.casefold()
        )
        return ToolOutput("\n".join(names[:200]) or "Aucune application.")


class NoArgs(ToolArgs):
    pass


class RunningApps(Tool[NoArgs]):
    name: ClassVar[str] = "applications_ouvertes"
    description: ClassVar[str] = "Liste les applications ouvertes en ce moment."
    Args: ClassVar[type[ToolArgs]] = NoArgs
    level: ClassVar[Level] = Level.LIRE
    os_control: ClassVar[bool] = True

    def __init__(self, catalog: AppCatalog) -> None:
        self._catalog = catalog

    def describe(self, args: NoArgs) -> str:
        del args
        return "Voir les applications ouvertes"

    async def run(self, args: NoArgs, ctx: ToolContext) -> ToolOutput:
        del args, ctx
        entries = await anyio.to_thread.run_sync(self._catalog.entries)
        running = await anyio.to_thread.run_sync(applications.running, entries)
        # Des entrées du menu partagent parfois un programme (Chrome et ses applis web).
        groups: dict[tuple[int, ...], list[applications.AppEntry]] = {}
        for entry, pids in running:
            groups.setdefault(tuple(pids), []).append(entry)
        lines = sorted(
            f"{', '.join(e.label for e in group)} — "
            f"{'même programme, ' if len(group) > 1 else ''}{len(pids)} processus"
            for pids, group in groups.items()
        )
        return ToolOutput("\n".join(lines) or "Aucune application reconnue n'est ouverte.")


class AppArgs(ToolArgs):
    nom: str = Field(min_length=1, max_length=80, description="Nom de l'application.")


class OpenApp(Tool[AppArgs]):
    name: ClassVar[str] = "ouvrir_application"
    description: ClassVar[str] = "Ouvre une application installée (par son nom)."
    Args: ClassVar[type[ToolArgs]] = AppArgs
    level: ClassVar[Level] = Level.AGIR
    os_control: ClassVar[bool] = True

    def __init__(self, catalog: AppCatalog) -> None:
        self._catalog = catalog

    def describe(self, args: AppArgs) -> str:
        try:
            return f"Ouvrir l'application {self._catalog.find(args.nom).label}"
        except NotFoundError:
            return f"Ouvrir l'application « {args.nom} »"

    async def run(self, args: AppArgs, ctx: ToolContext) -> ToolOutput:
        del ctx
        entry = self._catalog.find(args.nom)
        await applications.launch(entry)
        return ToolOutput(f"Application ouverte : {entry.name}.", {"id": entry.id})


class CloseApp(Tool[AppArgs]):
    name: ClassVar[str] = "fermer_application"
    description: ClassVar[str] = (
        "Ferme une application ouverte (elle peut te demander d'enregistrer d'abord)."
    )
    Args: ClassVar[type[ToolArgs]] = AppArgs
    level: ClassVar[Level] = Level.AGIR
    os_control: ClassVar[bool] = True

    def __init__(self, catalog: AppCatalog) -> None:
        self._catalog = catalog

    def describe(self, args: AppArgs) -> str:
        try:
            entry = self._catalog.find(args.nom)
        except NotFoundError:
            return f"Fermer l'application « {args.nom} »"
        text = f"Fermer l'application {entry.label}"
        others = applications.same_program(self._catalog.entries(), entry)
        if others:
            names = ", ".join(e.name for e in others[:3]) + (" …" if len(others) > 3 else "")
            text += f" — même programme que {names} : tout se fermera ensemble"
        return text

    async def run(self, args: AppArgs, ctx: ToolContext) -> ToolOutput:
        del ctx
        entry = self._catalog.find(args.nom)
        closed, left = await applications.close(entry)
        if left:
            return ToolOutput(
                f"{entry.name} : {closed} processus fermé(s), {left} encore ouvert(s) "
                "(peut-être une fenêtre qui demande d'enregistrer)."
            )
        return ToolOutput(f"Application fermée : {entry.name}.")


class UrlArgs(ToolArgs):
    adresse: str = Field(
        min_length=1, max_length=2000, description="Adresse web complète (http ou https)."
    )


def web_address(raw: str) -> str:
    """Seules les adresses http(s) avec un nom : jamais file:, javascript: ni une commande."""
    parts = urlsplit(raw.strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise InvalidInputError("Seules les adresses http et https s'ouvrent dans ton navigateur.")
    return parts.geturl()


class OpenUrl(Tool[UrlArgs]):
    name: ClassVar[str] = "ouvrir_adresse"
    description: ClassVar[str] = (
        "Ouvre une adresse web dans le navigateur de l'utilisateur, à l'écran "
        "(une pull request, une page à lui montrer)."
    )
    Args: ClassVar[type[ToolArgs]] = UrlArgs
    level: ClassVar[Level] = Level.AGIR
    os_control: ClassVar[bool] = True

    def describe(self, args: UrlArgs) -> str:
        return f"Ouvrir {args.adresse.strip()} dans ton navigateur"

    async def run(self, args: UrlArgs, ctx: ToolContext) -> ToolOutput:
        del ctx
        url = web_address(args.adresse)
        await applications.open_url(url)
        return ToolOutput(f"Ouvert dans ton navigateur : {url}")


class MoveArgs(ToolArgs):
    source: str = Field(min_length=1, max_length=1000, description="Fichier ou dossier à déplacer.")
    destination: str = Field(min_length=1, max_length=1000, description="Nouvel emplacement.")


class MoveFile(Tool[MoveArgs]):
    name: ClassVar[str] = "deplacer_fichier"
    description: ClassVar[str] = (
        "Déplace ou renomme un fichier ou un dossier de ton dossier personnel."
    )
    Args: ClassVar[type[ToolArgs]] = MoveArgs
    level: ClassVar[Level] = Level.AGIR
    os_control: ClassVar[bool] = True

    def describe(self, args: MoveArgs) -> str:
        return f"Déplacer {args.source} vers {args.destination}"

    async def run(self, args: MoveArgs, ctx: ToolContext) -> ToolOutput:
        source = resolve_user_path(args.source, ctx.home)
        target = resolve_user_path(args.destination, ctx.home)
        if not await anyio.Path(source).exists():
            raise NotFoundError(f"Introuvable : {args.source}.")
        if await anyio.Path(target).is_dir():
            target = target / source.name
        if await anyio.Path(target).exists():
            raise InvalidInputError(f"{args.destination} existe déjà : rien n'a été écrasé.")
        await anyio.Path(target.parent).mkdir(parents=True, exist_ok=True)
        await anyio.to_thread.run_sync(shutil.move, str(source), str(target))
        return ToolOutput(
            f"Déplacé : {shown(source, ctx.home)} → {shown(target, ctx.home)}",
            {"destination": str(target)},
        )


class CopyFile(Tool[MoveArgs]):
    name: ClassVar[str] = "copier_fichier"
    description: ClassVar[str] = "Copie un fichier ou un dossier de ton dossier personnel."
    Args: ClassVar[type[ToolArgs]] = MoveArgs
    level: ClassVar[Level] = Level.AGIR
    os_control: ClassVar[bool] = True

    def describe(self, args: MoveArgs) -> str:
        return f"Copier {args.source} vers {args.destination}"

    async def run(self, args: MoveArgs, ctx: ToolContext) -> ToolOutput:
        source = resolve_user_path(args.source, ctx.home)
        target = resolve_user_path(args.destination, ctx.home)
        if not await anyio.Path(source).exists():
            raise NotFoundError(f"Introuvable : {args.source}.")
        if await anyio.Path(target).is_dir():
            target = target / source.name
        if await anyio.Path(target).exists():
            raise InvalidInputError(f"{args.destination} existe déjà : rien n'a été écrasé.")
        await anyio.Path(target.parent).mkdir(parents=True, exist_ok=True)
        if await anyio.Path(source).is_dir():
            await anyio.to_thread.run_sync(lambda: shutil.copytree(source, target, symlinks=True))
        else:
            await anyio.to_thread.run_sync(shutil.copy2, source, target)
        return ToolOutput(
            f"Copié : {shown(source, ctx.home)} → {shown(target, ctx.home)}",
            {"destination": str(target)},
        )


class DirArgs(ToolArgs):
    chemin: str = Field(min_length=1, max_length=1000, description="Dossier à créer.")


class MakeDir(Tool[DirArgs]):
    name: ClassVar[str] = "creer_dossier"
    description: ClassVar[str] = "Crée un dossier dans ton dossier personnel."
    Args: ClassVar[type[ToolArgs]] = DirArgs
    level: ClassVar[Level] = Level.AGIR
    os_control: ClassVar[bool] = True

    def describe(self, args: DirArgs) -> str:
        return f"Créer le dossier {args.chemin}"

    async def run(self, args: DirArgs, ctx: ToolContext) -> ToolOutput:
        path = resolve_user_path(args.chemin, ctx.home)
        await anyio.Path(path).mkdir(parents=True, exist_ok=True)
        return ToolOutput(f"Dossier prêt : {shown(path, ctx.home)}")


def desktop_tools(
    router: ModelRouter,
    screen_mode: Callable[[], ScreenMode],
    *,
    grab: Callable[[], Awaitable[bytes]] = capture,
) -> list[Tool[Any]]:
    """Les outils du contrôle du PC. Trouver, cliquer et filmer à l'écran viennent de
    l'extension O-jarvis Shell (`ojarvis_shell.tools.screen_actions`)."""
    catalog = AppCatalog()
    tools: list[Tool[Any]] = [
        ListApps(catalog),
        RunningApps(catalog),
        OpenApp(catalog),
        CloseApp(catalog),
        OpenUrl(),
        LookAtScreen(router, screen_mode, grab),
        MoveFile(),
        CopyFile(),
        MakeDir(),
    ]
    return tools
