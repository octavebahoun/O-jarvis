"""Le profil « Tout Meta » (#47), construit pendant le hackathon Meta (O-jarvis Shell) : chaque
tâche n'a que des modèles de Meta, secours compris. Il s'inscrit dans les profils du routeur
d'O-jarvis, qui refuse alors tout modèle d'un autre éditeur (`is_meta`).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

from ojarvis.llm.policy import PROFILES, Route
from ojarvis.llm.types import TaskType

#: Muse Spark Open, le modèle ouvert de Meta, chez Baseten.
MUSE_SPARK_OPEN = "meta-models/muse-spark-open-1026"

SPARK = Route("meta", "muse-spark-1.3")

SPARK_OPEN = Route("baseten", MUSE_SPARK_OPEN)

#: Profil « Tout Meta » (Réglages) : seules ces routes, jamais un modèle d'un autre éditeur,
#: même en secours. Si Muse Spark ne répond pas, Muse Spark Open prend le relais ; un modèle
#: imposé à la main ne compte que s'il est de Meta.
META_POLICY: Mapping[TaskType, tuple[Route, ...]] = {
    "voice": (SPARK, SPARK_OPEN),
    "chat": (SPARK, SPARK_OPEN),
    "reasoning": (replace(SPARK, thinking=True), replace(SPARK_OPEN, thinking=True)),
    "extract": (SPARK, SPARK_OPEN),
    "title": (SPARK, SPARK_OPEN),
    "vision": (SPARK, SPARK_OPEN),
    # Le relecteur n'est pas l'auteur : Muse Wonder, le plus fort, puis Muse Spark Open.
    "review": (Route("meta", "muse-wonder-1.0", thinking=True), replace(SPARK_OPEN, thinking=True)),
}


PROFILES["meta"] = META_POLICY
