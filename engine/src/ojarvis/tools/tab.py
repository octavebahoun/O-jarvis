"""Voir et agir dans ton Chrome, par l'extension d'O-jarvis : lister tes onglets, en lire un,
y cliquer, remplir un champ, ouvrir une adresse, le fermer. L'extension fait l'action (voir
`ojarvis.chrome.bridge`) ; ces outils n'existent que quand elle est branchée.

Les actions passent toujours par un plan. Un paiement, un achat, une suppression ou un champ
sensible est de niveau 3 : jamais fait sans demander.
"""

from __future__ import annotations

import re
from typing import Any, ClassVar

from pydantic import Field, ValidationError

from ojarvis.chrome.bridge import ChromeBridge, TabAction
from ojarvis.chrome.page import PageIn
from ojarvis.core.errors import EngineError
from ojarvis.tools.base import Level, Tool, ToolArgs, ToolContext, ToolOutput

#: Ce qui engage ou détruit : payer, acheter, commander, supprimer, un mot de passe, une carte.
RISKY = re.compile(
    r"pa(?:y|yer|iement)|achet|achat|command|checkout|\bbuy|order|purchase|abonn|subscri"
    r"|supprim|effac|delete|remove|résili|virement|transf|mot de passe|password|carte|card"
    r"|cvv|cvc|iban|bancaire",
    re.IGNORECASE,
)


def risk_level(*texts: str) -> Level:
    return Level.DETRUIRE if any(RISKY.search(t) for t in texts) else Level.AGIR


class TabActionError(EngineError):
    """L'extension n'a pas pu faire l'action (élément disparu, champ refusé…)."""

    status_code = 409
    code = "tab_action_failed"


class TabTool[A: ToolArgs](Tool[A]):
    chrome_tab: ClassVar[bool] = True
    kind: ClassVar[str]

    def __init__(self, bridge: ChromeBridge) -> None:
        self._bridge = bridge

    async def _do(self, args: A) -> Any:
        action = TabAction(self.kind, args.model_dump(), int(self.level_for(args)))
        result = await self._bridge.run(action)
        if not result.ok:
            raise TabActionError(result.text)
        return result

    async def run(self, args: A, ctx: ToolContext) -> ToolOutput:
        del ctx
        result = await self._do(args)
        # Ce que l'onglet affiche ensuite vient de la page : une donnée.
        return ToolOutput(result.text, {"action": self.kind}, external=True, source="onglet")


class NoTabArgs(ToolArgs):
    pass


class TabList(TabTool[NoTabArgs]):
    name: ClassVar[str] = "chrome_onglets"
    description: ClassVar[str] = (
        "Liste les onglets ouverts dans le Chrome de l'utilisateur (numéro, titre, adresse)."
    )
    Args: ClassVar[type[ToolArgs]] = NoTabArgs
    level: ClassVar[Level] = Level.LIRE
    kind: ClassVar[str] = "lister"

    def describe(self, args: NoTabArgs) -> str:
        del args
        return "Lister tes onglets Chrome"

    async def run(self, args: NoTabArgs, ctx: ToolContext) -> ToolOutput:
        del ctx
        result = await self._do(args)
        tabs = result.data.get("tabs", [])
        lines = [
            f"[{t.get('id')}] {t.get('title', '')} — {t.get('url', '')}"
            + (" (onglet regardé)" if t.get("active") else "")
            for t in tabs
            if isinstance(t, dict)
        ]
        text = "\n".join(lines) or "Aucun onglet ouvert."
        return ToolOutput(text, {"onglets": len(lines)}, external=True, source="chrome")


class TabArgs(ToolArgs):
    onglet: int = Field(ge=0, description="Numéro de l'onglet (chrome_onglets, ou la page lue).")


class TabRead(TabTool[TabArgs]):
    name: ClassVar[str] = "onglet_lire"
    description: ClassVar[str] = (
        "Lit un onglet du Chrome de l'utilisateur : texte visible et éléments numérotés où "
        "agir. Sur les sites qu'il a autorisés depuis le panneau d'O-jarvis."
    )
    Args: ClassVar[type[ToolArgs]] = TabArgs
    level: ClassVar[Level] = Level.LIRE
    kind: ClassVar[str] = "lire"

    def describe(self, args: TabArgs) -> str:
        return f"Lire l'onglet n° {args.onglet}"

    async def run(self, args: TabArgs, ctx: ToolContext) -> ToolOutput:
        del ctx
        result = await self._do(args)
        try:
            page = PageIn.model_validate({**result.data.get("page", {}), "tab_id": args.onglet})
        except ValidationError as error:
            raise TabActionError("L'onglet a rendu une page illisible.") from error
        view = page.page()
        return ToolOutput(view.body(), {"url": view.url}, external=True, source=view.url)


class ElementArgs(TabArgs):
    ref: int = Field(ge=1, le=100_000, description="Numéro de l'élément dans la liste de la page.")
    cible: str = Field(
        min_length=1, max_length=200, description="Texte de l'élément, tel que la liste le montre."
    )


class TabClick(TabTool[ElementArgs]):
    name: ClassVar[str] = "onglet_cliquer"
    description: ClassVar[str] = (
        "Clique sur un lien ou un bouton d'un onglet Chrome de l'utilisateur, désigné par son "
        "numéro et son texte dans la liste des éléments de la page lue."
    )
    Args: ClassVar[type[ToolArgs]] = ElementArgs
    level: ClassVar[Level] = Level.AGIR
    kind: ClassVar[str] = "cliquer"

    def level_for(self, args: ElementArgs) -> Level:
        return risk_level(args.cible)

    def describe(self, args: ElementArgs) -> str:
        return f"Cliquer sur « {args.cible} » dans l'onglet n° {args.onglet}"


class FillTabArgs(ElementArgs):
    valeur: str = Field(max_length=2000, description="Texte à écrire dans le champ.")
    envoyer: bool = Field(
        default=False, description="Valider le champ ensuite (comme la touche Entrée)."
    )


class TabFill(TabTool[FillTabArgs]):
    name: ClassVar[str] = "onglet_remplir"
    description: ClassVar[str] = (
        "Écrit dans un champ d'un onglet Chrome de l'utilisateur, désigné par son numéro et "
        "son texte dans la liste des éléments ; jamais un mot de passe ni une carte bancaire."
    )
    Args: ClassVar[type[ToolArgs]] = FillTabArgs
    level: ClassVar[Level] = Level.AGIR
    kind: ClassVar[str] = "remplir"

    def level_for(self, args: FillTabArgs) -> Level:
        return risk_level(args.cible, args.valeur)

    def describe(self, args: FillTabArgs) -> str:
        then = " puis valider" if args.envoyer else ""
        return (
            f"Écrire « {args.valeur[:80]} » dans « {args.cible} »{then} (onglet n° {args.onglet})"
        )


class OpenTabArgs(TabArgs):
    url: str = Field(
        min_length=8, max_length=2000, pattern=r"^https?://", description="Adresse http(s)."
    )


class TabOpen(TabTool[OpenTabArgs]):
    name: ClassVar[str] = "onglet_ouvrir"
    description: ClassVar[str] = "Ouvre une adresse dans un onglet Chrome de l'utilisateur."
    Args: ClassVar[type[ToolArgs]] = OpenTabArgs
    level: ClassVar[Level] = Level.AGIR
    kind: ClassVar[str] = "ouvrir"

    def describe(self, args: OpenTabArgs) -> str:
        return f"Ouvrir {args.url} dans l'onglet n° {args.onglet}"


class TabClose(TabTool[TabArgs]):
    name: ClassVar[str] = "onglet_fermer"
    description: ClassVar[str] = "Ferme un onglet du Chrome de l'utilisateur."
    Args: ClassVar[type[ToolArgs]] = TabArgs
    level: ClassVar[Level] = Level.AGIR
    kind: ClassVar[str] = "fermer"

    def describe(self, args: TabArgs) -> str:
        return f"Fermer l'onglet n° {args.onglet}"


def tab_tools(bridge: ChromeBridge) -> list[Tool[Any]]:
    return [
        TabList(bridge),
        TabRead(bridge),
        TabClick(bridge),
        TabFill(bridge),
        TabOpen(bridge),
        TabClose(bridge),
    ]
