"""La mémoire des impasses du mode shell : un correctif qui a échoué n'est pas retenté.

Quand un correctif n'a pas réglé un échec, Jarvis note l'impasse, par projet : l'erreur, ce
qui a été tenté, pourquoi ça n'a pas marché. Cette mémoire ne remplit pas le contexte : elle
n'y entre jamais d'office (`ojarvis.context.engine` l'écarte). Jarvis la consulte au bon
moment, quand un test échoue et avant de proposer un correctif ; seules les impasses de ce
projet qui ressemblent à l'erreur reviennent, en quelques lignes. Comme tout souvenir, elle se
voit et se supprime dans Mémoire.
"""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import Field

from ojarvis.memory.store import MemoryKind, MemoryStore
from ojarvis.tools.base import Level, Tool, ToolArgs, ToolContext, ToolOutput
from ojarvis.tools.guards import resolve_user_path, shown

KIND: MemoryKind = "impasse"
#: Impasses rendues au plus par consultation : quelques lignes, pas un historique.
MAX_FOUND = 3


def project_tag(folder: str) -> str:
    return f"[{folder}]"


class NoteArgs(ToolArgs):
    dossier: str = Field(min_length=1, max_length=1000, description="Le dossier du projet.")
    erreur: str = Field(
        min_length=3,
        max_length=300,
        description="L'échec, reconnaissable : le test et l'erreur (« test_liste_vide : "
        "ZeroDivisionError »).",
    )
    tentative: str = Field(min_length=3, max_length=300, description="Le correctif tenté.")
    raison: str = Field(min_length=3, max_length=300, description="Pourquoi il n'a pas marché.")


class NoteImpasse(Tool[NoteArgs]):
    name: ClassVar[str] = "noter_impasse"
    description: ClassVar[str] = (
        "Note une impasse du projet : un correctif que tu as appliqué n'a pas réglé l'échec. "
        "Elle sera retrouvée par consulter_impasses dans une prochaine session, pour ne pas "
        "retenter la même chose."
    )
    Args: ClassVar[type[ToolArgs]] = NoteArgs
    level: ClassVar[Level] = Level.LIRE
    shell_only: ClassVar[bool] = True

    def __init__(self, memory: MemoryStore) -> None:
        self._memory = memory

    def describe(self, args: NoteArgs) -> str:
        return f"Noter une impasse dans {args.dossier} : {args.erreur}"

    async def run(self, args: NoteArgs, ctx: ToolContext) -> ToolOutput:
        folder = shown(resolve_user_path(args.dossier, ctx.home), ctx.home)
        content = (
            f"{project_tag(folder)} Erreur : {args.erreur} — Tenté : {args.tentative} — "
            f"Échec : {args.raison}"
        )
        record, created = await self._memory.add(KIND, content, source="mode shell")
        verb = "notée" if created else "mise à jour"
        return ToolOutput(f"Impasse {verb} pour {folder}.", {"id": record.id})


class ConsultArgs(ToolArgs):
    dossier: str = Field(min_length=1, max_length=1000, description="Le dossier du projet.")
    erreur: str = Field(
        min_length=3, max_length=300, description="L'échec à résoudre (test, erreur, message)."
    )


class ConsultImpasses(Tool[ConsultArgs]):
    name: ClassVar[str] = "consulter_impasses"
    description: ClassVar[str] = (
        "Cherche les impasses déjà notées pour ce projet et cette erreur : à faire quand un "
        "test échoue, avant de proposer un correctif, pour ne pas retenter ce qui a échoué."
    )
    Args: ClassVar[type[ToolArgs]] = ConsultArgs
    level: ClassVar[Level] = Level.LIRE
    shell_only: ClassVar[bool] = True

    def __init__(self, memory: MemoryStore, *, floor: float) -> None:
        self._memory = memory
        #: Similarité minimale par le sens (celle du contexte, selon le modèle d'embeddings).
        self._floor = floor

    def describe(self, args: ConsultArgs) -> str:
        return f"Consulter les impasses de {args.dossier} : {args.erreur}"

    async def run(self, args: ConsultArgs, ctx: ToolContext) -> ToolOutput:
        folder = shown(resolve_user_path(args.dossier, ctx.home), ctx.home)
        tag = project_tag(folder)
        found: list[Any] = []
        for hit in await self._memory.search(args.erreur, k=12):
            record = hit.record
            if record.kind != KIND or not record.content.startswith(tag):
                continue
            if hit.via == "sens" and hit.score < self._floor:
                continue  # trop loin de cette erreur
            found.append(record)
            if len(found) == MAX_FOUND:
                break
        if not found:
            return ToolOutput(f"Aucune impasse notée pour cette erreur dans {folder}.")
        await self._memory.touch([record.id for record in found])
        lines = [f"- {record.content[len(tag) :].strip()}" for record in found]
        text = f"Impasses déjà notées dans {folder} (ne retente pas ces correctifs) :\n"
        return ToolOutput(text + "\n".join(lines), {"ids": [record.id for record in found]})


def impasse_tools(memory: MemoryStore, *, floor: float) -> list[Tool[Any]]:
    return [NoteImpasse(memory), ConsultImpasses(memory, floor=floor)]
