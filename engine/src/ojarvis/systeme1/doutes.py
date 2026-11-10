"""« Mettre à jour un souvenir ? » et « Information incertaine ? » : un doute se lève en posant
la question dans la réponse (choix d'Octave, 10/11/2026), et la mémoire attend la réponse.

- Mise à jour : le message est comparé au souvenir le plus proche, s'il est pertinent.
  « remplacer » : le fait nouveau remplace l'ancien. « ajouter », « garder_les_deux » : l'ancien
  reste. « demander » : Jarvis demande si l'ancien est toujours vrai, et le garde en attendant ;
  le message suivant, qui répond, tranche.
- Incertain : « je crois que… » ne devient pas un souvenir ; Jarvis demande, ou vérifie un
  fait général.

Dans le doute (confiance basse), le modèle d'extraction décide seul, comme avant.
"""

from __future__ import annotations

from dataclasses import dataclass

from ojarvis.memory.store import SEPARATE_KINDS, MemoryHit, MemoryRecord
from ojarvis.systeme1.decideurs import SEUIL_RIEN, Systeme1

#: « remplacer » efface l'ancien souvenir : mesuré le 10/11/2026, aucun faux à 0,6 sur 90 paires.
SEUIL_REMPLACER = 0.6
#: Garder l'ancien (et demander) ne perd rien : à 0,5, une question de trop sur 90 paires, et
#: « embauché chez Free » face à Orange passe (0,56 avec le vrai modèle).
SEUIL_GARDER = 0.5
#: Mesuré le 10/11/2026 sur les phrases de test : à 0,5, aucune erreur sur 76.
SEUIL_INCERTAIN = 0.5
#: Ce que le modèle d'extraction doit affirmer pour qu'un fait d'un message incertain reste.
CONFIANCE_SI_INCERTAIN = 0.85


@dataclass(frozen=True)
class Doute:
    """Ce que le Système 1 sait, avec assez de confiance, d'un message pour la mémoire."""

    souvenir: MemoryRecord | None = None
    #: ajouter | remplacer | garder_les_deux | demander (avec `souvenir`)
    maj: str | None = None
    #: verifier | demander : l'utilisateur n'affirme pas nettement
    incertain: str | None = None

    @property
    def protege(self) -> int | None:
        """Le souvenir qui doit rester tel quel, quoi qu'en dise le modèle d'extraction."""
        if self.souvenir is None or self.maj in {None, "remplacer"}:
            return None
        return self.souvenir.id

    @property
    def remplace(self) -> int | None:
        return self.souvenir.id if self.souvenir is not None and self.maj == "remplacer" else None


@dataclass
class Attente:
    """Une question posée sur un souvenir : la réponse de l'utilisateur la tranchera."""

    souvenir: MemoryRecord
    #: La réponse de Jarvis qui contenait la question ; vide tant qu'elle n'est pas écrite.
    question: str = ""


def plus_proche(hits: list[MemoryHit], floor: float) -> MemoryRecord | None:
    """Le souvenir le plus proche du message, s'il en parle vraiment (par le sens)."""
    for hit in hits:
        if hit.record.kind not in SEPARATE_KINDS and hit.via != "mots" and hit.score >= floor:
            return hit.record
    return None


async def examiner(systeme1: Systeme1, message: str, hits: list[MemoryHit], floor: float) -> Doute:
    """Rien à signaler pour une question ou une tâche (« Apprendre ? » sûr de « rien »)."""
    decisions = await systeme1.lire(message)
    if not decisions:
        return Doute()
    apprendre = decisions.get("apprendre")
    if apprendre is not None and apprendre.reponse == "rien" and apprendre.confiance >= SEUIL_RIEN:
        return Doute()
    choix = decisions.get("incertain")
    incertain = None
    if choix is not None and choix.reponse != "accepter" and choix.confiance >= SEUIL_INCERTAIN:
        incertain = choix.reponse
    souvenir = plus_proche(hits, floor)
    if souvenir is None:
        return Doute(incertain=incertain)
    maj = await systeme1.comparer(souvenir.content, message)
    seuil = SEUIL_REMPLACER if maj is not None and maj.reponse == "remplacer" else SEUIL_GARDER
    if maj is None or maj.confiance < seuil:
        return Doute(incertain=incertain)
    return Doute(souvenir, maj.reponse, incertain)


def consignes(doute: Doute, *, poser: bool) -> list[str]:
    """Pour la réponse : la question à poser (une seule en attente par conversation)."""
    lignes = []
    if poser and doute.maj == "demander" and doute.souvenir is not None:
        lignes.append(
            f"Tu sais que « {doute.souvenir.content} » ; l'utilisateur dit autre chose sur le "
            "même sujet sans dire si c'est toujours vrai. Dans ta réponse, demande-le-lui en "
            "une courte phrase."
        )
    if doute.incertain == "demander":
        lignes.append(
            "L'utilisateur n'est pas sûr de ce qu'il dit de lui : ne le tiens pas pour acquis, "
            "et demande-lui de préciser si c'est utile."
        )
    elif doute.incertain == "verifier":
        lignes.append(
            "L'utilisateur avance un fait dont il doute : dis ce que tu en sais, ou vérifie-le "
            "si tu en as le moyen, avant de le reprendre."
        )
    return lignes


def pour_extraction(doute: Doute, attente: Attente | None) -> list[str]:
    """Pour le modèle d'extraction : ce que le Système 1 a tranché, et la question en attente."""
    lignes = []
    if attente is not None:
        lignes.append(
            f"Jarvis venait de demander si ce souvenir est toujours vrai : id "
            f"{attente.souvenir.id} « {attente.souvenir.content} » (sa réponse : "
            f"« {attente.question} »). Selon ce que répond l'utilisateur, remplace ce souvenir "
            "ou garde-le."
        )
    if doute.remplace is not None:
        lignes.append(f"Ce message rend faux le souvenir id {doute.remplace} : remplace-le.")
    elif doute.protege is not None:
        lignes.append(f"Le souvenir id {doute.protege} reste vrai : ne le remplace pas.")
    if doute.incertain is not None:
        lignes.append(
            "Ce message est incertain (« je crois », « peut-être ») : ne retiens que ce qui est "
            "affirmé nettement."
        )
    return lignes
