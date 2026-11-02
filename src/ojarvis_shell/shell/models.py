"""« /modele » : choisir le modèle de Meta qui répond dans ce terminal.

Le choix vaut pour cette session du terminal (pas pour l'app) : chaque demande l'envoie au
moteur, qui l'essaie d'abord. Le profil « Tout Meta » n'accepte que des modèles de Meta ; la
relecture du code reste à Muse Wonder.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Choice:
    key: str
    route: str  # « fournisseur/modèle », tel que le moteur le reçoit
    label: str
    what: str


CHOICES: tuple[Choice, ...] = (
    Choice("spark", "meta/muse-spark-1.3", "Muse Spark 1.3", "rapide, voit les images"),
    Choice("wonder", "meta/muse-wonder-1.0", "Muse Wonder 1.0", "le plus fort, pour coder"),
    Choice(
        "open",
        "baseten/meta-models/muse-spark-open-1026",
        "Muse Spark Open",
        "poids ouverts, chez Baseten",
    ),
)

AUTO = "auto"

#: Sans choix : Muse Spark répond, Muse Spark Open en secours.
DEFAULT_LABEL = "automatique (Muse Spark, Muse Spark Open en secours)"


def find(name: str) -> Choice | None:
    """Le modèle demandé, par son nom court ou le début de son nom (« won », « Muse Wonder »)."""
    lowered = name.strip().lower()
    wanted = lowered.removeprefix("muse ").removeprefix("muse-")
    if not wanted:
        return None
    return next(
        (c for c in CHOICES if c.key.startswith(wanted) or c.label.lower().startswith(lowered)),
        None,
    )


def label(route: str | None) -> str:
    """Le nom du modèle choisi, ou le choix automatique."""
    return next((c.label for c in CHOICES if c.route == route), DEFAULT_LABEL)
