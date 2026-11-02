"""La langue de la demande, dite au modèle quand c'est l'anglais.

Tout le reste du contexte est en français (consignes, mémoire) : le 2/11/2026, Muse Wonder a
répondu en français à une demande écrite en anglais. Un mot du moteur suffit à le remettre
dans la bonne langue. Une relance courte (« Continue. ») prend la langue de la demande d'avant.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

ENGLISH = frozenset(
    [
        "the",
        "and",
        "of",
        "to",
        "in",
        "is",
        "are",
        "was",
        "what",
        "how",
        "why",
        "please",
        "with",
        "for",
        "this",
        "that",
        "these",
        "my",
        "your",
        "our",
        "it",
        "its",
        "build",
        "create",
        "write",
        "make",
        "can",
        "could",
        "would",
        "should",
        "show",
        "tell",
        "me",
        "you",
        "there",
        "here",
        "left",
        "more",
        "page",
        "site",
        "file",
        "folder",
        "list",
    ]
)
FRENCH = frozenset(
    [
        "le",
        "la",
        "les",
        "un",
        "une",
        "des",
        "et",
        "du",
        "est",
        "sont",
        "quoi",
        "comment",
        "pourquoi",
        "avec",
        "pour",
        "ce",
        "cette",
        "ces",
        "mon",
        "ma",
        "mes",
        "ton",
        "ta",
        "tes",
        "notre",
        "fais",
        "crée",
        "écris",
        "peux",
        "veux",
        "dans",
        "sur",
        "il",
        "elle",
        "je",
        "tu",
        "nous",
        "vous",
        "reste",
        "fichier",
        "dossier",
        "liste",
    ]
)
WORD = re.compile(r"[a-zA-Zàâçéèêëîïôûùüÿœæ]+")

ENGLISH_HINT = (
    "Le dernier message de l'utilisateur est en anglais : réponds en anglais, même si les "
    "consignes et la mémoire sont en français."
)


def is_english(text: str) -> bool | None:
    """Vrai pour de l'anglais, faux pour du français, None si le texte ne le dit pas."""
    words = [w.lower() for w in WORD.findall(text)]
    english = sum(w in ENGLISH for w in words)
    french = sum(w in FRENCH for w in words)
    if english >= 2 and english > 2 * french:
        return True
    if french >= 2 and french > 2 * english:
        return False
    return None


def english_requested(texts: Iterable[str]) -> bool:
    """La demande la plus récente qui dit sa langue est-elle en anglais ?"""
    for text in texts:
        verdict = is_english(text)
        if verdict is not None:
            return verdict
    return False
