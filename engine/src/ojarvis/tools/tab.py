"""Agir dans ton onglet Chrome, celui que tu regardes à côté du panneau de Jarvis : cliquer,
remplir un champ, ouvrir une adresse, fermer l'onglet. Le panneau fait l'action (voir
`ojarvis.chrome.bridge`).

Proposés seulement dans le panneau de Chrome, et toujours dans un plan. Un paiement, un
achat, une suppression ou un champ sensible est de niveau 3 : jamais fait sans demander.
"""

from __future__ import annotations

import re
from typing import Any, ClassVar

from pydantic import Field

from ojarvis.chrome.bridge import ChromeBridge, TabAction
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


class TabTool[A: ToolArgs](Tool[A]):
    chrome_only: ClassVar[bool] = True
    kind: ClassVar[str]

    def __init__(self, bridge: ChromeBridge) -> None:
        self._bridge = bridge

    async def run(self, args: A, ctx: ToolContext) -> ToolOutput:
        del ctx
        action = TabAction(self.kind, args.model_dump(), int(self.level_for(args)))
        result = await self._bridge.run(action)
        if not result.ok:
            raise TabActionError(result.text)
        # Ce que l'onglet affiche ensuite vient de la page : une donnée.
        return ToolOutput(result.text, {"action": self.kind}, external=True, source="onglet")


class TabActionError(EngineError):
    """Le panneau n'a pas pu faire l'action (élément disparu, champ refusé…)."""

    status_code = 409
    code = "tab_action_failed"


class ElementArgs(ToolArgs):
    ref: int = Field(ge=1, le=100_000, description="Numéro de l'élément dans la liste de la page.")
    cible: str = Field(
        min_length=1, max_length=200, description="Texte de l'élément, tel que la liste le montre."
    )


class TabClick(TabTool[ElementArgs]):
    name: ClassVar[str] = "onglet_cliquer"
    description: ClassVar[str] = (
        "Clique sur un lien ou un bouton de l'onglet Chrome de l'utilisateur (la page ouverte "
        "à côté du panneau), désigné par son numéro et son texte dans la liste des éléments."
    )
    Args: ClassVar[type[ToolArgs]] = ElementArgs
    level: ClassVar[Level] = Level.AGIR
    kind: ClassVar[str] = "cliquer"

    def level_for(self, args: ElementArgs) -> Level:
        return risk_level(args.cible)

    def describe(self, args: ElementArgs) -> str:
        return f"Cliquer sur « {args.cible} » dans ton onglet"


class FillTabArgs(ElementArgs):
    valeur: str = Field(max_length=2000, description="Texte à écrire dans le champ.")
    envoyer: bool = Field(
        default=False, description="Valider le champ ensuite (comme la touche Entrée)."
    )


class TabFill(TabTool[FillTabArgs]):
    name: ClassVar[str] = "onglet_remplir"
    description: ClassVar[str] = (
        "Écrit dans un champ de l'onglet Chrome de l'utilisateur, désigné par son numéro et "
        "son texte dans la liste des éléments ; jamais un mot de passe ni une carte bancaire."
    )
    Args: ClassVar[type[ToolArgs]] = FillTabArgs
    level: ClassVar[Level] = Level.AGIR
    kind: ClassVar[str] = "remplir"

    def level_for(self, args: FillTabArgs) -> Level:
        return risk_level(args.cible, args.valeur)

    def describe(self, args: FillTabArgs) -> str:
        then = " puis valider" if args.envoyer else ""
        return f"Écrire « {args.valeur[:80]} » dans « {args.cible} »{then}, dans ton onglet"


class OpenTabArgs(ToolArgs):
    url: str = Field(
        min_length=8, max_length=2000, pattern=r"^https?://", description="Adresse http(s)."
    )


class TabOpen(TabTool[OpenTabArgs]):
    name: ClassVar[str] = "onglet_ouvrir"
    description: ClassVar[str] = "Ouvre une adresse dans l'onglet Chrome de l'utilisateur."
    Args: ClassVar[type[ToolArgs]] = OpenTabArgs
    level: ClassVar[Level] = Level.AGIR
    kind: ClassVar[str] = "ouvrir"

    def describe(self, args: OpenTabArgs) -> str:
        return f"Ouvrir {args.url} dans ton onglet"


class CloseTabArgs(ToolArgs):
    pass


class TabClose(TabTool[CloseTabArgs]):
    name: ClassVar[str] = "onglet_fermer"
    description: ClassVar[str] = "Ferme l'onglet Chrome de l'utilisateur (celui lu par le panneau)."
    Args: ClassVar[type[ToolArgs]] = CloseTabArgs
    level: ClassVar[Level] = Level.AGIR
    kind: ClassVar[str] = "fermer"

    def describe(self, args: CloseTabArgs) -> str:
        del args
        return "Fermer ton onglet"


def tab_tools(bridge: ChromeBridge) -> list[Tool[Any]]:
    return [TabClick(bridge), TabFill(bridge), TabOpen(bridge), TabClose(bridge)]
