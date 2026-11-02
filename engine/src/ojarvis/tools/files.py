"""Outils de fichiers, dans ton dossier personnel seulement, hors dossiers sensibles.

Lire et lister : niveau 1. Écrire (créer ou modifier) : niveau 2, après ta validation.
Supprimer : niveau 3, double confirmation ; le fichier part à la corbeille (récupérable).
"""

from __future__ import annotations

import asyncio
import difflib
import shutil
from pathlib import Path
from typing import ClassVar

import anyio
import anyio.to_thread
from pydantic import Field

from ojarvis.core.errors import InvalidInputError, NotFoundError, UpstreamError
from ojarvis.tools.base import (
    Level,
    Tool,
    ToolArgs,
    ToolContext,
    ToolOutput,
)
from ojarvis.tools.guards import resolve_user_path, shown

MAX_READ_BYTES = 200_000
MAX_WRITE_CHARS = 1_000_000
MAX_ENTRIES = 200
#: Un diff plus long est coupé : il reste lisible avant validation.
MAX_DIFF_CHARS = 8_000


def unified_diff(before: str, after: str, label: str) -> str:
    """Le diff unifié de `before` à `after`, comme `git diff`, coupé s'il est trop long."""
    lines = difflib.unified_diff(
        before.splitlines(), after.splitlines(), f"a/{label}", f"b/{label}", lineterm=""
    )
    text = "\n".join(lines)
    if len(text) > MAX_DIFF_CHARS:
        return text[:MAX_DIFF_CHARS] + "\n[… diff coupé]"
    return text or "(aucun changement)"


def _read_text(path: Path, raw: str) -> str:
    """Le texte d'un fichier existant, pour le modifier (jamais un binaire)."""
    if not path.is_file():
        raise NotFoundError(f"Fichier introuvable : {raw}.")
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise InvalidInputError(f"Fichier illisible : {raw} ({exc.strerror}).") from None
    if len(data) > MAX_WRITE_CHARS or b"\x00" in data[:8000]:
        raise InvalidInputError("Ce fichier n'est pas un texte modifiable.")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        # Le réécrire en UTF-8 abîmerait chaque accent hors du passage retouché.
        raise InvalidInputError(
            f"{raw} n'est pas en UTF-8 : une retouche abîmerait ses accents. Il n'est pas modifié."
        ) from None


class PathArgs(ToolArgs):
    chemin: str = Field(min_length=1, max_length=1000, description="Chemin (~ = dossier perso).")


class ReadFile(Tool[PathArgs]):
    name: ClassVar[str] = "lire_fichier"
    description: ClassVar[str] = "Lit un fichier texte de ton dossier personnel."
    Args: ClassVar[type[ToolArgs]] = PathArgs
    level: ClassVar[Level] = Level.LIRE

    def describe(self, args: PathArgs) -> str:
        return f"Lire le fichier {args.chemin}"

    async def run(self, args: PathArgs, ctx: ToolContext) -> ToolOutput:
        path = resolve_user_path(args.chemin, ctx.home)
        apath = anyio.Path(path)
        if not await apath.is_file():
            raise NotFoundError(f"Fichier introuvable : {args.chemin}.")
        raw = await apath.read_bytes()
        if b"\x00" in raw[:8000]:
            raise InvalidInputError("Ce fichier n'est pas un texte lisible.")
        text = raw[:MAX_READ_BYTES].decode("utf-8", "replace")
        if len(raw) > MAX_READ_BYTES:
            text += "\n[…fichier tronqué]"
        return ToolOutput(text, {"chemin": str(path)}, external=True, source=shown(path, ctx.home))


class ListArgs(PathArgs):
    caches: bool = Field(default=False, description="Inclure les fichiers cachés.")


class ListDirectory(Tool[ListArgs]):
    name: ClassVar[str] = "lister_dossier"
    description: ClassVar[str] = "Liste le contenu d'un dossier de ton dossier personnel."
    Args: ClassVar[type[ToolArgs]] = ListArgs
    level: ClassVar[Level] = Level.LIRE

    def describe(self, args: ListArgs) -> str:
        return f"Lister le dossier {args.chemin}"

    async def run(self, args: ListArgs, ctx: ToolContext) -> ToolOutput:
        path = resolve_user_path(args.chemin, ctx.home)
        apath = anyio.Path(path)
        if not await apath.exists():
            # Une réponse, pas une erreur : un plan qui va créer ce dossier ne s'arrête pas là.
            return ToolOutput(
                f"Ce dossier n'existe pas encore : {shown(path, ctx.home)}.",
                {"chemin": str(path), "existe": False},
            )
        if not await apath.is_dir():
            raise NotFoundError(f"Ce n'est pas un dossier : {args.chemin}.")

        def scan() -> list[str]:
            lines = []
            for entry in sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
                if entry.name.startswith(".") and not args.caches:
                    continue
                kind = "dossier" if entry.is_dir() else f"{entry.stat().st_size} octets"
                lines.append(f"{entry.name} ({kind})")
                if len(lines) >= MAX_ENTRIES:
                    lines.append("[…liste tronquée]")
                    break
            return lines

        lines = await anyio.to_thread.run_sync(scan)
        return ToolOutput("\n".join(lines) or "Dossier vide.", {"chemin": str(path)}, external=True)


class WriteArgs(PathArgs):
    contenu: str = Field(max_length=MAX_WRITE_CHARS, description="Texte à écrire.")


class WriteFile(Tool[WriteArgs]):
    name: ClassVar[str] = "ecrire_fichier"
    description: ClassVar[str] = (
        "Crée ou remplace un fichier texte dans ton dossier personnel. Pour ajouter du texte "
        "à un fichier existant, lis-le d'abord, puis écris son contenu complet."
    )
    Args: ClassVar[type[ToolArgs]] = WriteArgs
    level: ClassVar[Level] = Level.AGIR
    dynamic_fields: ClassVar[tuple[str, ...]] = ("contenu",)

    def describe(self, args: WriteArgs) -> str:
        preview = " ".join(args.contenu.split())
        preview = preview if len(preview) <= 80 else preview[:77] + "…"
        return f"Écrire dans le fichier {args.chemin} (créé ou remplacé) : « {preview} »"

    def preview(self, args: WriteArgs, home: Path) -> str | None:
        path = resolve_user_path(args.chemin, home)
        before = ""
        if path.exists():
            try:
                before = _read_text(path, args.chemin)
            except InvalidInputError:
                # Un gros fichier, un binaire : remplacé en entier, il n'y a pas de diff.
                return f"(remplace en entier {shown(path, home)}, qui n'est pas un texte lisible)"
        return unified_diff(before, args.contenu, shown(path, home))

    async def run(self, args: WriteArgs, ctx: ToolContext) -> ToolOutput:
        path = resolve_user_path(args.chemin, ctx.home)
        apath = anyio.Path(path)
        existed = await apath.exists()
        await apath.parent.mkdir(parents=True, exist_ok=True)
        await apath.write_text(args.contenu, encoding="utf-8")
        verb = "remplacé" if existed else "créé"
        return ToolOutput(
            f"Fichier {verb} : {shown(path, ctx.home)}", {"chemin": str(path), "cree": not existed}
        )


class DeleteFile(Tool[PathArgs]):
    name: ClassVar[str] = "supprimer_fichier"
    description: ClassVar[str] = "Met un fichier de ton dossier personnel à la corbeille."
    Args: ClassVar[type[ToolArgs]] = PathArgs
    level: ClassVar[Level] = Level.DETRUIRE

    def describe(self, args: PathArgs) -> str:
        return f"Mettre à la corbeille le fichier {args.chemin}"

    async def run(self, args: PathArgs, ctx: ToolContext) -> ToolOutput:
        path = resolve_user_path(args.chemin, ctx.home)
        if not await anyio.Path(path).exists():
            raise NotFoundError(f"Introuvable : {args.chemin}.")
        gio = shutil.which("gio")
        if gio is None:
            raise UpstreamError("Corbeille indisponible (gio absent) : rien n'a été supprimé.")
        process = await asyncio.create_subprocess_exec(
            gio, "trash", "--", str(path), stderr=asyncio.subprocess.PIPE
        )
        _, error = await process.communicate()
        if process.returncode != 0:
            raise UpstreamError(f"Mise à la corbeille impossible : {error.decode()[:200]}")
        return ToolOutput(f"Mis à la corbeille : {shown(path, ctx.home)}", {"chemin": str(path)})
