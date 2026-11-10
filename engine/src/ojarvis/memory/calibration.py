"""Les seuils qui dépendent du modèle de sens : chaque modèle note les ressemblances à sa façon.

Un seuil réglé pour un modèle ne vaut rien pour un autre : e5 notait presque tout entre 0,7
et 1, EmbeddingGemma 2 écarte bien plus les textes sans rapport. Ils sont donc mesurés par
modèle et rangés ici, ensemble.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Calibration:
    #: En dessous, un souvenir trouvé par le sens est écarté du contexte (trop peu pertinent).
    relevance_floor: float
    #: Au-dessus, un nouveau souvenir répète un souvenir existant : il s'y fond.
    duplicate: float
    #: Au-dessus, deux de tes demandes peuvent être « la même » (avec les mêmes noms propres).
    same_request: float
    #: Le score d'un souvenir trouvé seulement par les mots : juste sous le seuil de pertinence,
    #: pour qu'une bonne réponse par le sens passe devant.
    word_score: float


CALIBRATIONS: dict[str, Calibration] = {
    # EmbeddingGemma 2 q4, mesuré le 10/11/2026 sur des phrases françaises inventées :
    # bonnes réponses 0,69-0,85, mauvaises 0,65 en médiane et 0,69 au 9e décile ; un même fait
    # redit autrement 0,95-0,99, deux faits voisins 0,85-0,90 ; une même demande redite
    # 0,73-0,99, deux demandes différentes 0,59-0,80.
    "eg2": Calibration(relevance_floor=0.69, duplicate=0.94, same_request=0.85, word_score=0.67),
    # Mots hachés (les tests) : deux textes qui partagent quelques mots dépassent déjà 0,2.
    "hash": Calibration(relevance_floor=0.2, duplicate=0.95, same_request=0.95, word_score=0.78),
}

#: Un modèle sans mesure : des seuils prudents.
DEFAULT = Calibration(relevance_floor=0.5, duplicate=0.95, same_request=0.95, word_score=0.78)


def calibration(model_id: str) -> Calibration:
    return CALIBRATIONS.get(model_id, DEFAULT)
