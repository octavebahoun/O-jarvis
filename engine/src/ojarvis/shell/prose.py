"""La réponse de Jarvis au fil de l'eau : en retrait, coupée aux mots avant le bord du
terminal, avec le **gras**, le `code`, les titres et les blocs de code rendus (leurs marques
disparaissent). Le texte brut, lui, part tel quel vers la voix."""

from __future__ import annotations

import shutil
from collections.abc import Callable

from ojarvis.shell.display import BOLD, CYAN, DIM, Style

INDENT = "  "

#: Au-delà, une ligne se lit mal, même sur un grand écran.
PROSE_WIDTH = 96

FENCE = "```"


def prose_width(style: Style) -> int | None:
    """La largeur où couper les lignes ; aucune hors d'un vrai terminal (un tube, les tests)."""
    if not style.enabled:
        return None
    columns = shutil.get_terminal_size((PROSE_WIDTH, 24)).columns
    return max(40, min(columns - 1, PROSE_WIDTH))


class Prose:
    """Un paragraphe (ou plusieurs) de la réponse, écrit mot à mot à mesure qu'il arrive."""

    def __init__(self, write: Callable[[str], None], style: Style, width: int | None) -> None:
        self._write = write
        self._style = style
        self._width = width
        self._column = 0  # 0 : rien sur la ligne, le retrait vient avec le premier mot
        self._started = False
        self._word = ""
        self._space = False  # une espace attend le mot suivant
        self._breaks = 0  # des retours à la ligne attendent le mot suivant
        self._bold = False
        self._code = False
        self._heading = False
        #: Dans un bloc de code : la ligne en cours (None : hors d'un bloc).
        self._fence: str | None = None
        self._fence_head = False  # la fin de la ligne « ```python » est ignorée

    def feed(self, text: str) -> None:
        for char in text:
            if self._fence is not None:
                self._in_fence(char)
            elif char == "\n":
                if self._flush():
                    self._fence_head = False  # « ```python » finit ici
                    continue
                self._space = False
                self._bold = self._code = self._heading = False
                self._breaks += 1
            elif char in " \t":
                self._flush()
                self._space = True
            else:
                self._word += char

    def close(self) -> None:
        """La fin de la réponse : le dernier mot, la dernière ligne de code."""
        self._flush()
        if self._fence:
            self._code_line(self._fence)
        self._fence = None

    # ------------------------------------------------------------------ les mots

    def _flush(self) -> bool:
        """Écrit le mot en attente ; vrai s'il ouvre un bloc de code."""
        word, self._word = self._word, ""
        if not word:
            return False
        at_start = self._column == 0 or self._breaks > 0
        if at_start and word.startswith(FENCE):
            self._fence, self._fence_head = "", True
            return True
        if at_start and set(word) == {"#"} and len(word) <= 6:
            self._heading = True  # « ## Titre » : le titre en gras, sans ses dièses
            return False
        styled, length = self._render(word)
        if length == 0:
            return False
        self._emit_breaks()
        if self._column == 0:
            piece, self._column = INDENT + styled, len(INDENT) + length
        elif self._width is not None and self._column + 1 + length > self._width:
            piece, self._column = "\n" + INDENT + styled, len(INDENT) + length
        else:
            gap = " " if self._space else ""
            piece, self._column = gap + styled, self._column + len(gap) + length
        self._space = False
        self._started = True
        self._write(piece)
        return False

    def _render(self, word: str) -> tuple[str, int]:
        """Le mot en couleurs, sans ses marques ; et sa largeur à l'écran."""
        pieces: list[str] = []
        length = 0
        segment = ""

        def close_segment() -> None:
            nonlocal segment, length
            if segment:
                codes = [BOLD] if self._bold or self._heading else []
                if self._code:
                    codes.append(CYAN)
                pieces.append(self._style(segment, *codes))
                length += len(segment)
                segment = ""

        index = 0
        while index < len(word):
            if word.startswith("**", index):
                close_segment()
                self._bold = not self._bold
                index += 2
            elif word[index] == "`":
                close_segment()
                self._code = not self._code
                index += 1
            else:
                segment += word[index]
                index += 1
        close_segment()
        return "".join(pieces), length

    def _emit_breaks(self) -> None:
        """Les retours à la ligne en attente : un seul saut de ligne, ou une ligne vide entre
        deux paragraphes ; jamais avant le premier mot."""
        if not self._breaks:
            return
        breaks, self._breaks = self._breaks, 0
        if not self._started:
            return
        if self._column > 0:
            self._write("\n")
            self._column = 0
        if breaks >= 2:
            self._write("\n")

    # ------------------------------------------------------------------ le code

    def _in_fence(self, char: str) -> None:
        if char != "\n":
            self._fence = (self._fence or "") + char
            return
        line, self._fence = self._fence or "", ""
        if self._fence_head:
            self._fence_head = False
        elif line.strip().startswith(FENCE):
            self._fence = None
            self._breaks = 1
        else:
            self._code_line(line)

    def _code_line(self, line: str) -> None:
        """Une ligne d'un bloc de code, telle quelle (ses espaces comptent), sous un rail."""
        self._emit_breaks()
        if self._column > 0:
            self._write("\n")
        self._write(INDENT + self._style("│ ", DIM) + line + "\n")
        self._column = 0
        self._started = True
