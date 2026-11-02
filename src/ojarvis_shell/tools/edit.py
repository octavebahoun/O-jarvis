"""Retoucher un fichier (`modifier_fichier`), construit pendant le hackathon Meta (O-jarvis
Shell) : un passage exact remplacé, montré en diff avant ta validation, et relu par un autre
modèle en mode shell (`ojarvis_shell.agent.review`).
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

import anyio
import anyio.to_thread
from ojarvis.tools.base import (
    InvalidToolArgumentsError,
    Level,
    Tool,
    ToolArgs,
    ToolContext,
    ToolOutput,
)
from ojarvis.tools.files import MAX_WRITE_CHARS, PathArgs, _read_text, unified_diff
from ojarvis.tools.guards import resolve_user_path, shown
from pydantic import Field


class EditArgs(PathArgs):
    ancien: str = Field(
        min_length=1,
        max_length=MAX_WRITE_CHARS,
        description="Le passage exact à remplacer, copié du fichier (indentation comprise).",
    )
    nouveau: str = Field(
        max_length=MAX_WRITE_CHARS,
        description="Le texte qui le remplace (vide pour supprimer le passage).",
    )
    toutes: bool = Field(
        default=False,
        description="Remplacer toutes les occurrences ; sinon le passage doit être unique.",
    )


class EditFile(Tool[EditArgs]):
    """Une retouche ciblée : un passage exact remplacé, le diff montré avant validation."""

    name: ClassVar[str] = "modifier_fichier"
    description: ClassVar[str] = (
        "Modifie un fichier texte de ton dossier personnel en remplaçant un passage exact par "
        "un autre : une retouche ciblée, dont le diff est montré avant validation. Préfère-le "
        "à ecrire_fichier pour changer un fichier existant ; lis le fichier d'abord."
    )
    Args: ClassVar[type[ToolArgs]] = EditArgs
    level: ClassVar[Level] = Level.AGIR
    dynamic_fields: ClassVar[tuple[str, ...]] = ("nouveau",)

    def describe(self, args: EditArgs) -> str:
        removed = args.ancien.count("\n") + 1
        added = args.nouveau.count("\n") + 1 if args.nouveau else 0
        return f"Modifier le fichier {args.chemin} (−{removed} / +{added} lignes)"

    def _apply(self, text: str, args: EditArgs) -> str:
        count = text.count(args.ancien)
        if count == 0:
            raise InvalidToolArgumentsError(
                f"Le passage à remplacer est introuvable dans {args.chemin} : relis le fichier "
                "et copie le passage exactement, indentation comprise."
            )
        if count > 1 and not args.toutes:
            raise InvalidToolArgumentsError(
                f"Le passage apparaît {count} fois dans {args.chemin} : donne un passage plus "
                "long, unique, ou toutes=true."
            )
        return text.replace(args.ancien, args.nouveau, -1 if args.toutes else 1)

    def preview(self, args: EditArgs, home: Path) -> str | None:
        path = resolve_user_path(args.chemin, home)
        before = _read_text(path, args.chemin)
        return unified_diff(before, self._apply(before, args), shown(path, home))

    async def run(self, args: EditArgs, ctx: ToolContext) -> ToolOutput:
        path = resolve_user_path(args.chemin, ctx.home)
        # Relu au moment d'agir : le fichier a pu changer depuis l'aperçu.
        before = await anyio.to_thread.run_sync(_read_text, path, args.chemin)
        after = self._apply(before, args)
        await anyio.Path(path).write_text(after, encoding="utf-8")
        diff = unified_diff(before, after, shown(path, ctx.home))
        return ToolOutput(
            f"Fichier modifié : {shown(path, ctx.home)}\n{diff}",
            {"chemin": str(path)},
        )
