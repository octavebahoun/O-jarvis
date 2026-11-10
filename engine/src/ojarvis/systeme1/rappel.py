"""« Récupérer un souvenir ? » et « Chercher dans la bibliothèque ? » : n'envoyer au modèle que
ce qui sert à la question.

- Récupérer : quand la réponse est la même pour tout le monde (« Qui a écrit Les Misérables ? »),
  rien de ce que Jarvis sait de l'utilisateur n'est envoyé, sauf ce qu'il a épinglé.
- Bibliothèque : les anciennes conversations ne sont fouillées que quand la demande y renvoie
  (« reprends le script d'hier ») ; une longue conversation sans rapport n'y entre plus.
- Secours : une question sur un fait de l'utilisateur (« tu te souviens où j'habite ? ») va à la
  mémoire ; si elle ne trouve rien, le code fouille quand même la bibliothèque.

Dans le doute, le contexte fait comme avant : il cherche partout, et garde ce qui est pertinent.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from ojarvis.systeme1.decideurs import Choix

#: Mesuré le 10/11/2026 sur les phrases de test : « non » juste 16 fois sur 16, à tout seuil.
SEUIL_RECUPERER = 0.5
#: Mesuré le 10/11/2026 : un « non » faux à 0,62 (un hôtel conseillé il y a longtemps), aucun
#: à partir de 0,7.
SEUIL_BIBLIOTHEQUE = 0.7


@dataclass(frozen=True)
class Rappel:
    """Ce que le contexte va chercher pour le modèle ; sans Système 1, tout, comme avant."""

    #: Les souvenirs sur l'utilisateur (profil, faits, préférences, graphe).
    sur_toi: bool = True
    #: Les anciennes conversations.
    bibliotheque: bool = True
    #: Fouiller la bibliothèque si la mémoire ne trouve rien de pertinent.
    secours: bool = False


def _sur(choix: Choix | None, reponse: str, seuil: float) -> bool:
    return choix is not None and choix.reponse == reponse and choix.confiance >= seuil


def rappel(decisions: Mapping[str, Choix] | None) -> Rappel:
    if not decisions:
        return Rappel()
    recuperer = decisions.get("recuperer")
    return Rappel(
        sur_toi=not _sur(recuperer, "non", SEUIL_RECUPERER),
        bibliotheque=not _sur(decisions.get("bibliotheque"), "non", SEUIL_BIBLIOTHEQUE),
        secours=recuperer is not None and recuperer.reponse == "oui",
    )
