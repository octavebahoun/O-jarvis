"""Le verdict d'une commande (son niveau et pourquoi), et les petits outils communs au
classement (`commands.py`) et aux règles par programme (`programs.py`).
"""

from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path

from ojarvis.tools.base import Level


@dataclass(frozen=True, slots=True)
class Verdict:
    level: Level
    #: Pourquoi ce niveau (le morceau le plus risqué), montré avec le plan à valider.
    reason: str


Special = Callable[[str, list[str], Path, Path, int], Verdict]


def _highest(verdicts: list[Verdict]) -> Verdict:
    return max(verdicts, key=lambda v: v.level)


def _skip_options(words: list[str], with_value: frozenset[str]) -> list[str]:
    index = 0
    while index < len(words) and words[index].startswith("-"):
        index += 2 if words[index] in with_value else 1
    return words[index:]


def _sub(args: list[str], with_value: frozenset[str] = frozenset()) -> tuple[str, list[str]]:
    """La sous-commande (« git status » → « status ») et ce qui la suit."""
    rest = _skip_options(args, with_value)
    return (rest[0], rest[1:]) if rest else ("", [])


#: Les dossiers où la ligne s'est déplacée (`cd`) : ses chemins relatifs s'y lisent aussi.
_PLACES: ContextVar[tuple[Path, ...]] = ContextVar("places", default=())


def _in_order(wanted: tuple[str, ...], words: list[str]) -> bool:
    """« sts get-session-token » dans « --profile x sts get-session-token » aussi."""
    remaining = iter(words)
    return all(any(word == part for word in remaining) for part in wanted)
