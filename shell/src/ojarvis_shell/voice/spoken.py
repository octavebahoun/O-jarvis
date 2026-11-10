"""Le texte tel qu'il se dit : chiffres, unités, symboles et sigles écrits en toutes lettres.

MMS (la voix de Meta) ne connaît que les lettres : « 6,8 Go » doit lui arriver en « six
virgule huit gigaoctets », et « PC » en « pé cé ». Du code simple, pas un modèle.
"""

from __future__ import annotations

import re

UNITS = (
    "zéro",
    "un",
    "deux",
    "trois",
    "quatre",
    "cinq",
    "six",
    "sept",
    "huit",
    "neuf",
    "dix",
    "onze",
    "douze",
    "treize",
    "quatorze",
    "quinze",
    "seize",
)
TENS = {2: "vingt", 3: "trente", 4: "quarante", 5: "cinquante", 6: "soixante"}

#: Après un nombre : (singulier, pluriel). Le pluriel commence à 2, comme à l'écrit.
UNIT_WORDS: dict[str, tuple[str, str]] = {
    "%": ("pour cent", "pour cent"),
    "o": ("octet", "octets"),
    "Ko": ("kilooctet", "kilooctets"),
    "Mo": ("mégaoctet", "mégaoctets"),
    "Go": ("gigaoctet", "gigaoctets"),
    "To": ("téraoctet", "téraoctets"),
    "K": ("kilooctet", "kilooctets"),
    "M": ("mégaoctet", "mégaoctets"),
    "G": ("gigaoctet", "gigaoctets"),
    "T": ("téraoctet", "téraoctets"),
    "KB": ("kilooctet", "kilooctets"),
    "MB": ("mégaoctet", "mégaoctets"),
    "GB": ("gigaoctet", "gigaoctets"),
    "TB": ("téraoctet", "téraoctets"),
    "€": ("euro", "euros"),
    "$": ("dollar", "dollars"),
    "°C": ("degré", "degrés"),
    "°": ("degré", "degrés"),
    "ms": ("milliseconde", "millisecondes"),
    "s": ("seconde", "secondes"),
    "min": ("minute", "minutes"),
    "h": ("heure", "heures"),
    "km": ("kilomètre", "kilomètres"),
    "m": ("mètre", "mètres"),
}
FEMININE = frozenset({"seconde", "secondes", "minute", "minutes", "heure", "heures"})

LETTERS = {
    "a": "a",
    "b": "bé",
    "c": "cé",
    "d": "dé",
    "e": "e",
    "f": "èf",
    "g": "gé",
    "h": "ache",
    "i": "i",
    "j": "ji",
    "k": "ka",
    "l": "èl",
    "m": "èm",
    "n": "èn",
    "o": "o",
    "p": "pé",
    "q": "ku",
    "r": "èr",
    "s": "èss",
    "t": "té",
    "u": "u",
    "v": "vé",
    "w": "double vé",
    "x": "iks",
    "y": "i grec",
    "z": "zèd",
}
#: Sigles qui contiennent une voyelle mais s'épellent (les autres, « RAM », se lisent).
SPELLED = frozenset(
    {"IA", "API", "URL", "USB", "OS", "UI", "SSH", "SSD", "CPU", "GPU", "IP", "VPN"}
)
SYMBOLS = {"&": " et ", "+": " plus ", "=": " égale ", "@": " arobase ", "’": "'"}

_GROUPED = r"\d{1,3}(?:[   ]\d{3})+"
_NUMBER = rf"(?:{_GROUPED}|\d+)(?:[.,]\d+)*"
_UNIT = "|".join(re.escape(unit) for unit in sorted(UNIT_WORDS, key=len, reverse=True))
_CLOCK = re.compile(r"\b(\d{1,2}) ?h ?(\d{2})\b")
_DOLLARS_FIRST = re.compile(rf"\$ ?({_NUMBER})")
_WITH_UNIT = re.compile(rf"(?<![\w.,])(-?{_NUMBER}) ?({_UNIT})(?![\w°])")
_ORDINAL = re.compile(r"\b(\d+) ?(er|re|ère|e|ème)\b")
_PLAIN = re.compile(rf"(?<![\w.,])-?{_NUMBER}")
_ACRONYM = re.compile(r"\b[A-Z]{2,5}\b")
_DIGITS = re.compile(r"\d+")


def _below_100(n: int) -> str:
    if n <= 16:
        return UNITS[n]
    tens, unit = divmod(n, 10)
    if tens == 1:
        return f"dix {UNITS[unit]}"
    if tens in (7, 9):  # la dizaine d'avant, puis de dix à dix-neuf
        base = "soixante" if tens == 7 else "quatre vingt"
        return base + (" et " if n == 71 else " ") + _below_100(10 + unit)
    if tens == 8:
        return "quatre vingts" if unit == 0 else f"quatre vingt {UNITS[unit]}"
    return TENS[tens] + ("" if unit == 0 else (" et " if unit == 1 else " ") + UNITS[unit])


def _below_1000(n: int) -> str:
    hundreds, rest = divmod(n, 100)
    if hundreds == 0:
        return _below_100(rest)
    head = "cent" if hundreds == 1 else f"{UNITS[hundreds]} cent"
    if rest == 0:
        return head + ("s" if hundreds > 1 else "")
    return f"{head} {_below_100(rest)}"


def _invariable(words: str) -> str:
    """« deux cents » et « quatre vingts » perdent leur s devant « mille »."""
    return words[:-1] if words.endswith(("cents", "vingts")) else words


def number_words(n: int) -> str:
    """Un entier en toutes lettres : 1024 → « mille vingt quatre »."""
    if n < 0:
        return f"moins {number_words(-n)}"
    if n == 0:
        return "zéro"
    parts: list[str] = []
    for size, name in ((10**9, "milliard"), (10**6, "million")):
        count, n = divmod(n, size)
        if count:
            parts.append(f"{number_words(count)} {name}{'s' if count > 1 else ''}")
    thousands, n = divmod(n, 1000)
    if thousands:
        parts.append("mille" if thousands == 1 else f"{_invariable(_below_1000(thousands))} mille")
    if n:
        parts.append(_below_1000(n))
    return " ".join(parts)


def _value(text: str) -> float:
    return float(re.sub(r"[   ]", "", text).replace(",", "."))


def _say_number(text: str) -> str:
    """« 6,8 » → « six virgule huit » ; « 3.14.0 » (une version) → « trois point quatorze… »."""
    sign = "moins " if text.startswith("-") else ""
    digits = re.sub(r"[   ]", "", text.lstrip("-"))
    if digits.count(".") > 1:
        return sign + " point ".join(_say_number(part) for part in digits.split("."))
    whole, _, decimals = digits.replace(".", ",").partition(",")
    words = number_words(int(whole))
    if decimals:
        zeros = len(decimals) - len(decimals.lstrip("0"))
        rest = decimals.lstrip("0")
        tail = " ".join(["zéro"] * zeros + ([number_words(int(rest))] if rest else []))
        words = f"{words} virgule {tail}"
    return sign + words


def _with_unit(number: str, unit: str) -> str:
    singular, plural = UNIT_WORDS[unit]
    name = singular if abs(_value(number)) < 2 else plural
    words = _say_number(number)
    if name in FEMININE and words.endswith("un"):
        words += "e"  # « vingt et une heures »
    return f"{words} {name}"


def _clock(match: re.Match[str]) -> str:
    hours = _with_unit(match.group(1), "h")
    minutes = match.group(2)
    return f"{hours} {_say_number(minutes)}" if minutes and int(minutes) else hours


def _ordinal(match: re.Match[str]) -> str:
    n = int(match.group(1))
    if n == 1:
        return "première" if match.group(2) in ("re", "ère") else "premier"
    words = _invariable(number_words(n))
    head, _, last = words.rpartition(" ")
    if last == "cinq":
        last = "cinqu"
    elif last == "neuf":
        last = "neuv"
    elif last.endswith("e"):
        last = last[:-1]
    return f"{head} {last}ième".strip()


def _acronym(match: re.Match[str]) -> str:
    word = match.group(0)
    if word in SPELLED or not re.search(r"[AEIOUY]", word):
        return " ".join(LETTERS[letter] for letter in word.lower())
    return word


def spoken_french(text: str) -> str:
    """Le texte à dire : plus aucun chiffre, et des sigles épelés."""
    text = _CLOCK.sub(_clock, text)
    text = _DOLLARS_FIRST.sub(lambda m: _with_unit(m.group(1), "$"), text)
    text = _WITH_UNIT.sub(lambda m: _with_unit(m.group(1), m.group(2)), text)
    text = _ORDINAL.sub(_ordinal, text)
    text = _PLAIN.sub(lambda m: _say_number(m.group(0)), text)
    # Les chiffres collés à des lettres (« i5 », « mp3 ») : nombre par nombre.
    text = _DIGITS.sub(lambda m: f" {number_words(int(m.group(0)))} ", text)
    text = _ACRONYM.sub(_acronym, text)
    for symbol, words in SYMBOLS.items():
        text = text.replace(symbol, words)
    return re.sub(r"\s+([.,;:!?])", r"\1", re.sub(r"\s+", " ", text)).strip()
