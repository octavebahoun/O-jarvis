"""« Sensible ? » : un message ordinaire, personnel ou secret.

- Secret = ce qui ouvre ou paie (mot de passe, PIN, CVV, IBAN, clé d'API, numéro de carte) :
  rien n'est gardé du message, même mêlé à un fait.
- Le décideur seul crie trop vite au secret : le 11/11/2026, sur de vrais messages, « mon
  opérateur mobile, c'est Orange » et un numéro de passeport sont sortis « secret » à 0,43 et
  0,44, et rien n'a été gardé. Il faut donc qu'il soit sûr, ou que le message ressemble à un
  secret (un mot comme « mot de passe », un numéro de carte, un IBAN). Un numéro de carte ou un
  IBAN est un secret même si le décideur ne le voit pas.
- Personnel (santé, argent, adresse, papiers d'identité) : gardé à part, envoyé au modèle
  seulement quand la question en parle.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from ojarvis.systeme1.decideurs import Choix

#: Au-dessus, le décideur suffit. Mesuré le 11/11/2026 sur les phrases de test (62 + 83).
SEUIL_SECRET = 0.6

_MOTS = re.compile(
    r"mots? de passe|\bmdp\b|password|passcode|\bpin\b|\bcvv\b|\bcvc\b|cryptogramme"
    r"|code (?:secret|confidentiel|d'acc[eè]s|de (?:ma |la |mon )?carte|de s[ée]curit[ée])"
    r"|\biban\b|\brib\b|cl[ée]s? (?:d'|d’)?api|api[ _-]?key|\btoken\b|jeton d'acc[eè]s"
    r"|phrase de r[ée]cup[ée]ration|seed phrase|identifiants? de connexion",
    re.IGNORECASE,
)
#: Un numéro de carte bancaire : 13 à 19 chiffres, espaces ou tirets permis. Un numéro de
#: téléphone (10 ou 11 chiffres) n'en est pas un.
_CARTE = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")
#: Un IBAN : deux lettres du pays, deux chiffres, puis des blocs de quatre.
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){3,7}\b")


def ressemble_a_un_secret(texte: str) -> bool:
    return bool(_MOTS.search(texte) or _CARTE.search(texte) or _IBAN.search(texte))


def secret(decisions: Mapping[str, Choix] | None, texte: str) -> bool:
    """Un secret : un numéro de carte ou un IBAN, ou le décideur qui y croit, sûr ou appuyé
    par un mot qui en parle."""
    if _CARTE.search(texte) or _IBAN.search(texte):
        return True
    choix = decisions.get("sensible") if decisions else None
    if choix is None or choix.reponse != "secret":
        return False
    return choix.confiance >= SEUIL_SECRET or bool(_MOTS.search(texte))


def personnel(decisions: Mapping[str, Choix] | None) -> bool:
    """Un « secret » écarté devient le plus probable des deux autres."""
    choix = decisions.get("sensible") if decisions else None
    if choix is None:
        return False
    if choix.reponse == "secret":
        return choix.proba("personnel") > choix.proba("ordinaire")
    return choix.reponse == "personnel"
