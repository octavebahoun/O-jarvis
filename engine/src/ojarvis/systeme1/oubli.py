"""« Oublier une information ? » : effacer de la mémoire ce que l'utilisateur demande d'oublier.

- Une demande d'oubli (« oublie que j'habite à Lyon ») : Jarvis dit ce qu'il oublie, ou demande
  quoi si c'est flou (« oublie ça ») ; après sa réponse, le modèle d'extraction désigne les
  souvenirs visés parmi ceux qu'on lui montre, et le code les efface. Il faut les deux : le
  Système 1 qui y voit un oubli, et le modèle qui nomme les souvenirs.
- « Oublie tout ce que tu sais sur moi » : rien n'est effacé avant un « oui » au message
  suivant (validé par Octave le 10/11/2026). Tout = les faits, préférences et profil, et le
  graphe ; pas la carte de la machine ni les impasses du mode shell.
- « garder » (« j'ai oublié mes clés », « supprime ce fichier ») : rien à voir avec la mémoire.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from ojarvis.systeme1.decideurs import Choix

#: Une demande d'oubli, claire ou floue, quand « garder » pèse moins que ce seuil. Mesuré le
#: 10/11/2026 : 47 demandes sur 48 prises au test ; « oublie tout ce que tu sais sur moi » (vu
#: « demander » à 0,81) et « oublie que je bosse chez Orange » (« demander » à 0,43) passent. Les
#: fausses alertes (« tu te souviens où j'habite ? », 0,37) n'effacent rien : les consignes
#: disent « si », et le modèle d'extraction doit encore nommer les souvenirs.
SEUIL_GARDER = 0.4

#: Un « oui » franc au début du message : tout autre réponse annule l'oubli total.
_OUI = re.compile(
    r"^\W*(oui|ouais|yes|vas[- ]y|confirme|je confirme|d'accord|ok|okay|efface|c'est sûr)\b",
    re.IGNORECASE,
)

CONSIGNE_EXTRACTION = (
    "Si l'utilisateur demande d'oublier ce que tu sais de lui : mets dans « oublie » les ids des "
    "souvenirs existants qu'il vise, et ne crée aucun fait pour cette demande ; « oublie_tout » "
    "seulement s'il veut effacer tout ce que tu sais de lui. Sinon, laisse-les vides."
)


def demande_oubli(decisions: Mapping[str, Choix] | None) -> bool:
    choix = decisions.get("oublier") if decisions else None
    return choix is not None and choix.proba("garder") < SEUIL_GARDER


def confirme(texte: str) -> bool:
    return bool(_OUI.match(texte))


def consignes(demande: bool, *, confirmation: bool | None) -> list[str]:
    """Pour la réponse. `confirmation` : la réponse attendue à « tout effacer ? » (None sans
    question en attente)."""
    if confirmation is True:
        return [
            "L'utilisateur confirme : tout ce que tu sais de lui est effacé juste après ta "
            "réponse. Dis-le-lui en une phrase."
        ]
    if confirmation is False:
        return ["L'utilisateur n'a pas confirmé : rien n'est effacé de ta mémoire. Dis-le-lui."]
    if demande:
        return [
            "Si l'utilisateur te demande d'oublier quelque chose de ce que tu sais de lui : "
            "dis-lui en une phrase ce que tu oublies (c'est fait juste après ta réponse), ou "
            "demande-lui quoi si ce n'est pas clair. S'il veut que tu oublies tout ce que tu sais "
            "de lui, ne dis pas que c'est fait : demande-lui de confirmer, car tout sera effacé."
        ]
    return []
