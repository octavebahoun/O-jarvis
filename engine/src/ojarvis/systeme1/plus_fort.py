"""« Proposer un modèle plus fort ? » : pour une tâche lourde (architecture, preuve, bug rare,
long document, plan sur des semaines), Jarvis propose de refaire la réponse avec un modèle plus
fort. Il propose, tu choisis : jamais de changement tout seul.

- Un plus fort est-il disponible ? C'est le code qui le dit : le premier modèle de
  raisonnement (le modèle de relais d'abord, en « Tout Meta »), configuré, en état de répondre,
  et autre que celui du chat.
- Tu refuses ? Chaque message est jugé seul : il repropose quand c'est utile.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from ojarvis.llm.policy import Route
from ojarvis.llm.router import ModelRouter
from ojarvis.systeme1.decideurs import Choix

#: Mesuré le 10/11/2026 sur les phrases de test : aucun faux, à tout seuil (48 sur 48).
SEUIL_PROPOSER = 0.5


@dataclass(frozen=True)
class PlusFort:
    #: « fournisseur/modèle », tel que `/v1/chat` l'accepte pour un message.
    model: str
    label: str


def proposer(decisions: Mapping[str, Choix] | None) -> bool:
    choix = decisions.get("modele_fort") if decisions else None
    return choix is not None and choix.reponse == "proposer" and choix.confiance >= SEUIL_PROPOSER


def plus_fort_disponible(
    router: ModelRouter, relais: Callable[[], tuple[Route, int] | None]
) -> PlusFort | None:
    regle = relais()
    forts = router.routes("reasoning", first=regle[0] if regle else None)
    if not forts:
        return None
    fort = forts[0]
    chat = router.routes("chat")
    if chat and chat[0].key == fort.key:
        return None  # le chat répond déjà avec le plus fort
    sante = router.health.get(fort.provider)
    if sante is not None and not sante.available(time.monotonic()):
        return None
    spec = router.catalog.get(fort.provider, fort.model)
    return PlusFort(fort.key, spec.label if spec else fort.model)
