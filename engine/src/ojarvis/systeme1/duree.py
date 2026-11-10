"""« Combien de temps ? » : un souvenir de quelques jours reçoit une échéance, passé laquelle il
s'efface seul de la mémoire (la conversation reste dans la bibliothèque).

Le décideur lit chaque fait extrait, pas le message entier : dans « je suis végétarien et j'ai
rendez-vous jeudi », seul le rendez-vous est passager. La date, c'est le code qui la garde : le
modèle d'extraction la donne en AAAA-MM-JJ, jamais Laya ni les décideurs.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, tzinfo

from ojarvis.memory.store import Echeance
from ojarvis.systeme1.decideurs import Choix

#: En dessous, un « quelques jours » ne suffit pas à effacer : se tromper ferait perdre un fait
#: durable. Mesuré le 10/11/2026 : 20 sur 20 au test, sans fausse alerte, dès 0.
SEUIL_COURT = 0.5
#: Après le jour dit, Jarvis sait encore quelques jours que « tu as eu rendez-vous ».
APRES_LA_DATE = timedelta(days=3)
#: Un état passager sans date (« j'ai la grippe ») : une semaine.
SANS_DATE = timedelta(days=7)
#: Jamais effacé le jour même où il est appris, même si la date dite est passée.
AU_MOINS = timedelta(days=1)


def jour_dit(texte: str | None) -> date | None:
    """La date donnée par le modèle d'extraction, si elle est lisible."""
    if not texte:
        return None
    try:
        return date.fromisoformat(texte.strip())
    except ValueError:
        return None


def echeance(choix: Choix | None, jour: date | None, maintenant: datetime, tz: tzinfo) -> Echeance:
    """Durable, sauf un « quelques jours » assez sûr : il s'efface 3 jours après le jour dit
    (à minuit, heure locale), ou une semaine après s'il n'y en a pas."""
    if choix is None or choix.reponse != "quelques_jours" or choix.confiance < SEUIL_COURT:
        return Echeance(None, jour)
    if jour is None:
        return Echeance(maintenant + SANS_DATE, None)
    fin = datetime.combine(jour + APRES_LA_DATE + timedelta(days=1), time.min, tzinfo=tz)
    return Echeance(max(fin, maintenant + AU_MOINS), jour)
