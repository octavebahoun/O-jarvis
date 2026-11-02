"""Ce qu'un plan rapporte : son résumé affiché (étapes, aperçus, mesures), le compte rendu que
le modèle relit au tour suivant, et les adresses ou sorties venues du web, gardées comme
données.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from ojarvis.agent.measures import MEASURE_AFTER, MEASURE_BEFORE
from ojarvis.agent.plans import (
    PlanRecord,
    StepRecord,
)
from ojarvis.extension import Review, talks_to_network

_URL = re.compile(r"https?://[^\s<>\"'()\[\]{}«»]+")


def urls_in(value: Any) -> set[str]:
    """Adresses web citées dans un texte (ou dans les valeurs d'un résultat structuré)."""
    if isinstance(value, str):
        return {u.rstrip(".,;:!?").rstrip("/") for u in _URL.findall(value)}
    if isinstance(value, dict):
        return set().union(*(urls_in(v) for v in value.values())) if value else set()
    if isinstance(value, list | tuple):
        return set().union(*(urls_in(v) for v in value)) if value else set()
    return set()


#: L'outil des commandes du mode shell (`ojarvis_shell.tools.shell.RunCommand`).
SHELL_TOOL = "executer_commande"


@dataclass(frozen=True, slots=True)
class RanCommand:
    """Une commande que l'utilisateur a lancée lui-même (sudo, proposée par Jarvis)."""

    command: str
    code: int
    output: str


@dataclass(frozen=True)
class PlanReport:
    """Les résultats d'un plan fini, rendus au modèle pour la suite du mode shell."""

    text: str
    #: La demande d'origine : la suite d'un plan n'a pour texte que « Continue. ».
    request: str
    #: Des sorties de commandes sont revenues : des données, jamais des consignes.
    tainted: bool = False
    #: L'une d'elles a parlé au réseau (curl, git clone…) : pas de « toujours » ensuite.
    web: bool = False
    urls: frozenset[str] = frozenset()
    #: Chaque commande lancée, et si elle a échoué : de quoi voir un test toujours rouge.
    commands: tuple[tuple[str, bool], ...] = ()


#: « code de sortie 1 (0,4 s) » dans la sortie d'une commande.
EXIT_CODE = re.compile(r"code de sortie (-?\d+)")


def command_failed(step: StepRecord) -> bool:
    """Une commande en échec : l'étape a échoué, ou la commande a rendu un code non nul."""
    if step.status == "failed":
        return True
    found = EXIT_CODE.search(step.result or "")
    return found is not None and found.group(1) != "0"


#: Sources de ta machine : un fichier, le bac à sable, les tests d'un skill.
LOCAL_SOURCES = frozenset({"bac à sable", "tests du skill", "carte de la machine"})


def from_web(source: str | None, command: str | None = None) -> bool:
    """Une donnée venue d'ailleurs que ta machine : une page, une recherche, une intégration
    (GitHub, Notion…), un serveur MCP, un agent, ton écran, ou une commande qui parle au
    réseau (curl, git clone…). Un contenu piégé peut s'y cacher."""
    if not source:
        return False
    if "://" in source:
        host = urlsplit(source).hostname or ""
        return host not in ("localhost", "127.0.0.1", "::1")
    if source.startswith(("/", "~")):
        return False  # un fichier de ta machine
    if source == "commande":
        return command is not None and talks_to_network(command)
    return source not in LOCAL_SOURCES


def plan_summary(
    plan: PlanRecord,
    previews: dict[int, str] | None = None,
    review: Review | None = None,
    *,
    web: bool = False,
) -> dict[str, Any]:
    """Le plan tel qu'il s'affiche : ses étapes (outil, commande, aperçu en diff), l'avis du
    relecteur, et s'il suit une lecture venue du web."""
    previews = previews or {}
    summary: dict[str, Any] = {
        "id": plan.id,
        "title": plan.title,
        "status": plan.status,
        "max_level": plan.max_level,
        "tainted": plan.tainted,
        "web": web,
        "steps": [
            {
                "position": s.position,
                "description": s.description,
                "level": s.level,
                "tool": s.tool,
                **(
                    {"command": str(s.arguments["commande"])}
                    if s.tool == "executer_commande" and "commande" in s.arguments
                    else {}
                ),
                **({"preview": previews[s.position]} if s.position in previews else {}),
                # Un clic à l'écran : le terminal montre où (la capture annotée du repère).
                **(
                    {"repere": str(s.arguments["repere"])}
                    if s.tool == "cliquer_ecran" and "repere" in s.arguments
                    else {}
                ),
                **(
                    {"measure": "avant" if s.why == MEASURE_BEFORE else "après"}
                    if s.why in (MEASURE_BEFORE, MEASURE_AFTER)
                    else {}
                ),
            }
            for s in plan.steps
        ],
    }
    if review is not None:
        summary["review"] = review.as_dict()
    return summary
