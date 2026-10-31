"""Outils de Jarvis : arguments à schéma strict, niveau de permission, description lisible.

Trois niveaux, appliqués par une seule couche (`ojarvis.permissions.gate`) :
1. lire : autonome (page web, fichier, recherche) ;
2. agir : ta validation d'abord (cliquer, envoyer, écrire un fichier, utiliser un cookie) ;
3. détruire ou s'étendre : double confirmation (supprimer, installer, changer une permission).
"""

from __future__ import annotations

import enum
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from ojarvis.core.errors import InvalidInputError
from ojarvis.llm.types import ToolSpec

if TYPE_CHECKING:
    from ojarvis.sandbox.runner import SandboxRunner


class Level(enum.IntEnum):
    LIRE = 1
    AGIR = 2
    DETRUIRE = 3


LEVEL_LABELS = {
    Level.LIRE: "lecture, sans validation",
    Level.AGIR: "action, après ta validation",
    Level.DETRUIRE: "destruction ou extension, double confirmation",
}


class ToolArgs(BaseModel):
    """Arguments d'un outil : aucun champ inconnu accepté."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


@dataclass(frozen=True)
class FollowUp:
    """Action que l'outil lui-même propose ensuite : elle devient un plan qui t'attend.

    Seul le code d'un outil peut en créer une, jamais un contenu lu (page, fichier, écran)."""

    tool: str
    arguments: dict[str, Any]
    why: str


@dataclass(frozen=True)
class ToolOutput:
    #: Ce que le modèle lira (s'il vient de l'extérieur, il sera marqué comme donnée).
    text: str
    #: Résultat structuré, réutilisable par les étapes suivantes d'un plan.
    data: dict[str, Any] = field(default_factory=dict)
    #: Contenu venu du web, d'un fichier ou d'une page : une donnée, jamais une instruction.
    external: bool = False
    source: str | None = None
    follow_up: FollowUp | None = None


@dataclass
class ToolContext:
    http: httpx.AsyncClient
    secret: Callable[[str], str | None]
    home: Path
    sandbox: SandboxRunner | None = None
    #: Hôtes privés autorisés malgré la garde réseau (serveur local des tests seulement).
    allowed_private_hosts: tuple[str, ...] = ()
    #: Proxy de garde (adresses publiques seulement, DNS épinglé) pour lire le web.
    fetch_proxy: str | None = None


class InvalidToolArgumentsError(InvalidInputError):
    code = "invalid_tool_arguments"


class Tool[A: ToolArgs](ABC):
    name: ClassVar[str]
    description: ClassVar[str]
    Args: ClassVar[type[ToolArgs]]
    level: ClassVar[Level]
    #: Arguments qui peuvent reprendre le résultat d'une étape précédente ({{etape N}}) dans
    #: une action (niveau 2 ou 3). La cible (adresse, fichier, bouton) doit, elle, être
    #: connue au moment de ta validation.
    dynamic_fields: ClassVar[tuple[str, ...]] = ()
    #: Contrôle du PC (applications, écran, gestion de fichiers) : désactivé par défaut,
    #: tu l'actives dans Réglages.
    os_control: ClassVar[bool] = False
    #: Mode shell seulement (lancer une commande) : jamais proposé ni accepté ailleurs.
    shell_only: ClassVar[bool] = False
    #: Voit et agit dans ton Chrome par l'extension d'O-jarvis : seulement quand elle est branchée.
    chrome_tab: ClassVar[bool] = False

    def parse(self, raw: dict[str, Any]) -> A:
        try:
            args: A = self.Args.model_validate(raw)  # type: ignore[assignment]
        except ValidationError as exc:
            first = exc.errors()[0]
            where = ".".join(str(p) for p in first.get("loc", ())) or "arguments"
            raise InvalidToolArgumentsError(
                f"Arguments invalides pour {self.name} ({where} : {first.get('msg')})."
            ) from exc
        return args

    def level_for(self, args: A) -> Level:
        """Le niveau peut dépendre des arguments (par défaut : celui de l'outil)."""
        del args
        return self.level

    @abstractmethod
    def describe(self, args: A) -> str:
        """Ce qui va se passer, et sur quoi : montré avant validation et écrit au journal."""

    def preview(self, args: A, home: Path) -> str | None:
        """Ce que l'action changerait (un diff), montré avant ta validation ; None sinon.

        Peut refuser une action qui ne s'appliquerait pas (un passage introuvable) : le modèle
        corrige avant que tu voies le plan."""
        del args, home
        return None

    @abstractmethod
    async def run(self, args: A, ctx: ToolContext) -> ToolOutput: ...

    def spec(self) -> ToolSpec:
        schema = self.Args.model_json_schema()
        schema.pop("title", None)
        label = LEVEL_LABELS[self.level]
        return ToolSpec(
            self.name, f"{self.description} (niveau {int(self.level)} : {label})", schema
        )


class UnknownToolError(InvalidInputError):
    code = "unknown_tool"


class ToolRegistry:
    def __init__(self, tools: list[Tool[Any]] | None = None) -> None:
        self._tools: dict[str, Tool[Any]] = {}
        for tool in tools or []:
            self.register(tool)

    def register(self, tool: Tool[Any]) -> None:
        if tool.name in self._tools:
            raise ValueError(f"outil déjà enregistré : {tool.name}")
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> None:
        """Retire un outil (un skill désactivé, un serveur MCP désinstallé)."""
        self._tools.pop(name, None)

    def get(self, name: str) -> Tool[Any]:
        tool = self._tools.get(name)
        if tool is None:
            raise UnknownToolError(f"Outil inconnu : {name}.")
        return tool

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    def all(self) -> list[Tool[Any]]:
        return list(self._tools.values())

    def specs(
        self,
        *,
        max_level: Level,
        os_control: bool = True,
        shell_mode: bool = False,
        chrome: bool = False,
    ) -> list[ToolSpec]:
        return [
            t.spec()
            for t in self._tools.values()
            if t.level <= max_level
            and (os_control or not t.os_control)
            and (shell_mode or not t.shell_only)
            and (chrome or not t.chrome_tab)
        ]

    def catalog(self) -> list[dict[str, Any]]:
        return [
            {
                "name": t.name,
                "description": t.description,
                "level": int(t.level),
                "level_label": LEVEL_LABELS[t.level],
                "os_control": t.os_control,
                "chrome_tab": t.chrome_tab,
            }
            for t in self._tools.values()
        ]
