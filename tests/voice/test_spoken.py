"""Le texte tel que MMS doit le recevoir : plus aucun chiffre, des sigles épelés."""

from __future__ import annotations

import re

import pytest

from ojarvis.voice.spoken import number_words, spoken_french


@pytest.mark.parametrize(
    ("number", "words"),
    [
        (0, "zéro"),
        (17, "dix sept"),
        (21, "vingt et un"),
        (71, "soixante et onze"),
        (77, "soixante dix sept"),
        (80, "quatre vingts"),
        (81, "quatre vingt un"),
        (91, "quatre vingt onze"),
        (200, "deux cents"),
        (280_000, "deux cent quatre vingt mille"),
        (1990, "mille neuf cent quatre vingt dix"),
        (2_000_000_000, "deux milliards"),
        (-3, "moins trois"),
    ],
)
def test_numbers_are_written_the_french_way(number: int, words: str) -> None:
    assert number_words(number) == words


@pytest.mark.parametrize(
    ("text", "spoken"),
    [
        ("Docker pèse 6,8G.", "Docker pèse six virgule huit gigaoctets."),
        ("Soit 21 %.", "Soit vingt et un pour cent."),
        ("1 Go libre", "un gigaoctet libre"),
        ("1 024 fichiers", "mille vingt quatre fichiers"),
        ("Rendez-vous à 21 h 30.", "Rendez-vous à vingt et une heures trente."),
        ("Le 1er et le 21e essai.", "Le premier et le vingt et unième essai."),
        ("Ça a coûté $3.26.", "Ça a coûté trois virgule vingt six dollars."),
        ("Il fait -3 °C.", "Il fait moins trois degrés."),
        ("Python 3.14.0", "Python trois point quatorze point zéro"),
        ("Ton PC et l'IA, pas la RAM.", "Ton pé cé et l'i a, pas la RAM."),
        ("Un CPU i5.", "Un cé pé u i cinq."),
    ],
)
def test_what_mms_receives_is_what_should_be_said(text: str, spoken: str) -> None:
    assert spoken_french(text) == spoken


def test_no_digit_is_left_for_mms() -> None:
    text = "Le 4/11 à 21 h, 3 fichiers de 1,5 Mo, version 2.0, 100 % et 7e place, mp3."
    assert not re.search(r"\d", spoken_french(text))
