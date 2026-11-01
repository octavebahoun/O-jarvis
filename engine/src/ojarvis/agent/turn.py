"""Ce qui se passe pendant un tour de l'agent (outils utilisés, plans proposés, adresses
connues) et le résultat d'un appel d'outil."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ojarvis.agent.plans import (
    PlanRecord,
)
from ojarvis.core.errors import EngineError


@dataclass
class AgentTurn:
    """Ce qui s'est passé pendant un tour : outils utilisés, plans proposés."""

    #: Un contenu externe a été lu : un plan proposé ensuite est signalé à la validation.
    tainted: bool = False
    #: Tour du mode shell : seul à pouvoir lancer une commande (`executer_commande`).
    shell: bool = False
    #: Tour venu de loin (Telegram) ou sans toi (automatisation) : tes onglets Chrome ne
    #: s'y lisent ni ne s'y touchent.
    away: bool = False
    #: Un contenu venu d'ailleurs a été lu (page, recherche, intégration, curl…) : « toujours »
    #: ne s'applique pas.
    web: bool = False
    tools: list[dict[str, Any]] = field(default_factory=list)
    plans: list[str] = field(default_factory=list)
    #: Adresses connues pendant ce tour : celles de ta demande et celles lues (liens, résultats).
    known_urls: set[str] = field(default_factory=set)

    def knows(self, url: str) -> bool:
        return url.strip().rstrip("/") in self.known_urls


class ShellOnlyError(EngineError):
    status_code = 403
    code = "shell_only"


class ChromeAwayError(EngineError):
    status_code = 403
    code = "chrome_away"


@dataclass(frozen=True)
class CallOutcome:
    content: str
    event: dict[str, Any]
    is_error: bool = False
    plan: PlanRecord | None = None
