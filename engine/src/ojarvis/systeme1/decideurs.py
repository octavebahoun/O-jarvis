"""Les décideurs du Système 1 : une petite régression logistique par décision, posée sur
l'empreinte EmbeddingGemma du message, celle que la mémoire calcule déjà pour chercher.

Une seule lecture du message suffit donc à toutes les décisions, en une milliseconde. Les poids
(`decideurs-eg2.npz`) sont appris par `laya/decideurs.py` sur des phrases inventées ; ils ne
valent que pour les vecteurs du modèle qui les a produits.

Le 10/11/2026, Laya Multilingual affiné faisait aussi bien (95 % contre 94 % sur le test) mais
relisait le message pour chaque question : 6,8 s par message sur un i5 de 2015.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import numpy.typing as npt

from ojarvis.core.logs import get_logger
from ojarvis.memory.embeddings import Embedder

log = get_logger(__name__)

POIDS = Path(__file__).with_name("decideurs-eg2.npz")
#: Les décisions qui comparent un souvenir au message : elles lisent deux vecteurs.
PAIRES = frozenset({"mise_a_jour"})
#: Au-dessus de cette confiance, un « rien » de « Apprendre ? » évite l'extraction. Mesuré le
#: 10/11/2026 sur les phrases de test : à 0,5, 13 « rien » sur 21 évités, aucun fait perdu.
SEUIL_RIEN = 0.5

Vecteur = npt.NDArray[np.float32]


@dataclass(frozen=True)
class Choix:
    reponse: str
    confiance: float
    #: La probabilité de chaque réponse (la plus probable est `reponse`).
    probas: Mapping[str, float] = field(default_factory=dict, compare=False)

    def proba(self, reponse: str) -> float:
        return self.probas.get(reponse, self.confiance if reponse == self.reponse else 0.0)


class Decideurs:
    """Les poids de chaque décision et ses réponses possibles, pour un modèle de sens donné."""

    def __init__(
        self, model_id: str, poids: Mapping[str, Vecteur], reponses: Mapping[str, list[str]]
    ) -> None:
        self.model_id = model_id
        self._poids = dict(poids)
        self._reponses = dict(reponses)

    @classmethod
    def charger(cls, chemin: Path = POIDS) -> Decideurs:
        with np.load(chemin, allow_pickle=False) as fichier:
            noms = [n for n in fichier.files if not n.startswith("_") and "." not in n]
            return cls(
                str(fichier["_modele"]),
                {n: fichier[n].astype(np.float32) for n in noms},
                {n: [str(r) for r in fichier[f"{n}.reponses"]] for n in noms},
            )

    @property
    def decisions(self) -> tuple[str, ...]:
        return tuple(self._poids)

    def decider(self, decision: str, traits: Vecteur) -> Choix:
        poids = self._poids[decision]
        scores = np.append(traits, np.float32(1.0)) @ poids  # le biais est la dernière ligne
        probas = np.exp(scores - scores.max())
        probas /= probas.sum()
        meilleur = int(probas.argmax())
        reponses = self._reponses[decision]
        return Choix(
            reponses[meilleur],
            float(probas[meilleur]),
            {r: float(p) for r, p in zip(reponses, probas, strict=True)},
        )

    def sur_message(self, vecteur: Vecteur) -> dict[str, Choix]:
        """Toutes les décisions qui ne lisent que le message."""
        return {d: self.decider(d, vecteur) for d in self._poids if d not in PAIRES}

    def mise_a_jour(self, souvenir: Vecteur, message: Vecteur) -> Choix:
        traits = np.concatenate([souvenir, message, souvenir * message, np.abs(souvenir - message)])
        return self.decider("mise_a_jour", traits.astype(np.float32))


class Systeme1:
    """Lit un message une fois et rend ses décisions ; muet si les poids ne vont pas avec le
    modèle de sens en service (les tests, le hachage) ou si le modèle n'est pas prêt."""

    def __init__(self, embedder: Embedder, decideurs: Decideurs | None) -> None:
        self._embedder = embedder
        self._decideurs = decideurs

    @property
    def actif(self) -> bool:
        return self._decideurs is not None and self._decideurs.model_id == self._embedder.model_id

    async def lire(self, message: str) -> dict[str, Choix] | None:
        if self._decideurs is None or not self.actif:
            return None
        try:
            vecteur = (await self._embedder.embed([message], "query"))[0]
        except Exception:
            log.warning("systeme1_unavailable")
            return None
        return self._decideurs.sur_message(vecteur)

    async def comparer(self, souvenir: str, message: str) -> Choix | None:
        """« Mettre à jour un souvenir ? » : le souvenir est relu avec le préfixe de recherche,
        celui de l'apprentissage (la mémoire, elle, le range avec le préfixe « passage »)."""
        if self._decideurs is None or not self.actif:
            return None
        if "mise_a_jour" not in self._decideurs.decisions:
            return None
        try:
            souvenir_v, message_v = await self._embedder.embed([souvenir, message], "query")
        except Exception:
            log.warning("systeme1_unavailable")
            return None
        return self._decideurs.mise_a_jour(souvenir_v, message_v)

    async def decider(self, decision: str, textes: list[str]) -> list[Choix] | None:
        """Une décision sur chacun des textes (les faits extraits d'un message, par exemple) ;
        None si le Système 1 se tait ou ne connaît pas cette décision."""
        if self._decideurs is None or not self.actif or decision not in self._decideurs.decisions:
            return None
        if not textes:
            return []
        try:
            vecteurs = await self._embedder.embed(textes, "query")
        except Exception:
            log.warning("systeme1_unavailable")
            return None
        return [self._decideurs.decider(decision, v) for v in vecteurs]


def charger_systeme1(embedder: Embedder) -> Systeme1:
    """Le Système 1 du moteur ; sans poids lisibles, il reste muet et Jarvis fait comme avant."""
    try:
        decideurs: Decideurs | None = Decideurs.charger()
    except (OSError, KeyError, ValueError):
        log.warning("systeme1_weights_unreadable")
        decideurs = None
    return Systeme1(embedder, decideurs)
