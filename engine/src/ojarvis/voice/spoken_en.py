"""L'anglais pour la voix MMS anglaise : reconnaître une phrase anglaise (Jarvis répond dans la
langue de la question), et l'écrire comme on la dit, sans chiffres ni symboles."""

from __future__ import annotations

import re

ONES = [
    "zero",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
]
TENS = ["_", "_", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]

#: Des mots qui ne trompent pas sur la langue d'une phrase (« on », « me » sont aux deux).
FRENCH_WORDS = frozenset(
    [
        "le",
        "la",
        "les",
        "des",
        "un",
        "une",
        "est",
        "et",
        "je",
        "tu",
        "il",
        "elle",
        "nous",
        "vous",
        "pas",
        "que",
        "qui",
        "pour",
        "avec",
        "dans",
        "sur",
        "ce",
        "cette",
        "c'est",
        "j'ai",
        "du",
        "au",
        "aux",
        "mais",
        "ou",
        "donc",
        "oui",
        "non",
        "bonjour",
        "merci",
        "voici",
        "ton",
        "ta",
        "tes",
        "mon",
        "ma",
        "mes",
        "sont",
        "fait",
        "faire",
        "peux",
        "veux",
    ]
)
ENGLISH_WORDS = frozenset(
    [
        "the",
        "and",
        "is",
        "are",
        "you",
        "your",
        "to",
        "of",
        "it",
        "this",
        "that",
        "with",
        "for",
        "i",
        "i'm",
        "it's",
        "what",
        "how",
        "yes",
        "no",
        "hello",
        "thanks",
        "here",
        "my",
        "we",
        "they",
        "be",
        "can",
        "will",
        "was",
        "were",
        "have",
        "has",
        "there",
        "which",
        "would",
        "should",
    ]
)
_ACCENTS = re.compile(r"[àâçéèêëîïôûùüÿœæ]", re.IGNORECASE)
_WORD = re.compile(r"[a-z']+")
_NUMBER = re.compile(r"(?<![\w.])-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|(?<![\w.])-?\d+(?:\.\d+)?")
_ACRONYM = re.compile(r"\b[A-Z]{2,5}\b")
_DIGITS = re.compile(r"\d+")
#: Des unités qu'on dit en entier, plutôt que lettre par lettre.
UNITS = {"KB": "kilobytes", "MB": "megabytes", "GB": "gigabytes", "TB": "terabytes"}
SYMBOLS = {
    "%": " percent",
    "€": " euros",
    "&": " and ",
    "+": " plus ",
    "=": " equals ",
    "@": " at ",
}


def english(sentence: str) -> bool:
    """Une phrase anglaise ? Les accents disent le français ; sinon, les petits mots."""
    if _ACCENTS.search(sentence):
        return False
    words = _WORD.findall(sentence.lower())
    return sum(w in ENGLISH_WORDS for w in words) > sum(w in FRENCH_WORDS for w in words)


def _below_1000(n: int) -> str:
    hundreds, rest = divmod(n, 100)
    parts = [f"{ONES[hundreds]} hundred"] if hundreds else []
    if rest >= 20:
        tens, ones = divmod(rest, 10)
        parts.append(TENS[tens] + (f" {ONES[ones]}" if ones else ""))
    elif rest or not parts:
        parts.append(ONES[rest])
    return " ".join(parts)


def number_words(n: int) -> str:
    """Un entier en toutes lettres : 1024 → « one thousand twenty four »."""
    if n < 0:
        return f"minus {number_words(-n)}"
    if n < 1000:
        return _below_1000(n)
    parts: list[str] = []
    for size, name in ((10**9, "billion"), (10**6, "million"), (1000, "thousand")):
        count, n = divmod(n, size)
        if count:
            parts.append(f"{number_words(count)} {name}")
    if n:
        parts.append(_below_1000(n))
    return " ".join(parts)


def _say_number(text: str) -> str:
    """« 3.5 » → « three point five » ; « 1,024 » → « one thousand twenty four »."""
    sign = "minus " if text.startswith("-") else ""
    whole, _, decimals = text.lstrip("-").replace(",", "").partition(".")
    words = number_words(int(whole))
    if decimals:
        words += " point " + " ".join(ONES[int(d)] for d in decimals)
    return sign + words


def spoken_english(text: str) -> str:
    """Le texte à dire : plus aucun chiffre, des sigles épelés, des symboles en mots."""
    text = re.sub(r"\$ ?(-?\d[\d,]*(?:\.\d+)?)", r"\1 dollars", text)
    text = _NUMBER.sub(lambda m: _say_number(m.group(0)), text)
    # Les chiffres collés à des lettres (« i5 », « mp3 ») : nombre par nombre.
    text = _DIGITS.sub(lambda m: f" {number_words(int(m.group(0)))} ", text)
    text = _ACRONYM.sub(lambda m: UNITS.get(m.group(0)) or " ".join(m.group(0).lower()), text)
    for symbol, words in SYMBOLS.items():
        text = text.replace(symbol, words)
    return re.sub(r"\s+([.,;:!?])", r"\1", re.sub(r"\s+", " ", text)).strip()
