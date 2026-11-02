"""Les politiques de routage : pour chaque type de tâche, la liste ordonnée des modèles à
essayer (par défaut, ou profil « Tout Meta », rien que des modèles de Meta), et les délais
avant de basculer.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from ojarvis.llm.types import (
    TaskType,
)

#: « N'importe quel modèle de ce fournisseur qui convient », dans l'ordre du catalogue
#: (utile pour les modèles gratuits d'OpenRouter, qui changent souvent).
ANY_MODEL = "*"


@dataclass(frozen=True, slots=True)
class Route:
    provider: str
    model: str
    thinking: bool = False

    @property
    def key(self) -> str:
        return f"{self.provider}/{self.model}"


@dataclass(frozen=True, slots=True)
class Relay:
    """Un modèle plus fort pour ce tour (relais après des échecs), et pourquoi : la raison
    s'affiche dans le Journal, sur l'appel au modèle."""

    route: Route
    reason: str


#: Politique par défaut. Rapide et bon marché pour la voix et le chat courant.
DEFAULT_POLICY: Mapping[TaskType, tuple[Route, ...]] = {
    "voice": (
        Route("deepseek", "deepseek-flash"),
        Route("anthropic", "claude-opus-5-5"),
        Route("openai", "gpt-5-mini"),
        Route("openrouter", ANY_MODEL),
        Route("ollama", "ministral-3:3b"),
    ),
    "chat": (
        Route("deepseek", "deepseek-flash"),
        Route("anthropic", "claude-opus-5-5"),
        Route("openai", "gpt-5"),
        Route("openrouter", ANY_MODEL),
        Route("ollama", "ministral-3:3b"),
    ),
    "reasoning": (
        Route("deepseek", "deepseek-v4-pro", thinking=True),
        Route("anthropic", "claude-opus-5-5", thinking=True),
        Route("openai", "gpt-5", thinking=True),
        Route("deepseek", "deepseek-flash", thinking=True),
        Route("openrouter", ANY_MODEL),
        Route("ollama", "ministral-3:3b"),
    ),
    "extract": (
        Route("deepseek", "deepseek-flash"),
        Route("anthropic", "claude-haiku-5-5"),
        Route("openai", "gpt-5-mini"),
        Route("openrouter", ANY_MODEL),
        Route("ollama", "ministral-3:3b"),
    ),
    "title": (
        Route("deepseek", "deepseek-flash"),
        Route("anthropic", "claude-haiku-5-5"),
        Route("openai", "gpt-5-mini"),
        Route("openrouter", ANY_MODEL),
        Route("ollama", "ministral-3:3b"),
    ),
    # Relecture d'une modification de code (mode shell) : un autre regard que l'auteur.
    "review": (
        Route("deepseek", "deepseek-v4-pro", thinking=True),
        Route("anthropic", "claude-opus-5-5", thinking=True),
        Route("openai", "gpt-5", thinking=True),
        Route("deepseek", "deepseek-flash", thinking=True),
        Route("openrouter", ANY_MODEL),
        Route("ollama", "ministral-3:3b"),
    ),
    "vision": (
        # Réflexion coupée : 1,5 s au lieu de 2,3 s à l'essai, pour la même lecture.
        Route("deepseek", "deepseek-v4-flash-vision-exp"),
        Route("openrouter", ANY_MODEL),
        Route("anthropic", "claude-opus-5-5"),
        Route("openai", "gpt-5"),
        Route("ollama", "ministral-3:3b"),
    ),
}

#: Les profils de modèles en plus d'« Automatique », par nom. Le profil « Tout Meta » vient de
#: l'extension O-jarvis Shell (`ojarvis_shell.llm.meta_profile`), qui s'y inscrit.
PROFILES: dict[str, Mapping[TaskType, tuple[Route, ...]]] = {}


def is_meta(route: Route) -> bool:
    """Un modèle de Meta : l'API de Meta, ou un modèle ouvert de Meta chez Baseten."""
    return route.provider == "meta" or (
        route.provider == "baseten" and route.model.startswith("meta-models/")
    )


#: Délai maximal avant le premier morceau de réponse, par tâche (fournisseurs en ligne).
#: Au-delà, on bascule : à la voix, attendre 30 s un premier mot n'a pas de sens.
FIRST_EVENT_TIMEOUT_S: Mapping[TaskType, float] = {
    "voice": 5.0,
    "chat": 20.0,
    "reasoning": 60.0,
    "extract": 30.0,
    "title": 20.0,
    "vision": 45.0,
    "review": 90.0,
}

#: Un modèle local peut devoir se charger en mémoire (jusqu'à ~50 s sur cette machine).
LOCAL_FIRST_EVENT_TIMEOUT_S = 120.0
