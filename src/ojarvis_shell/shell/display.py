"""Ce que le terminal affiche : couleurs, roue d'attente, aperçus de sortie, flux SSE du moteur."""

from __future__ import annotations

import itertools
import json
import shutil
import threading
import time
from collections.abc import Iterable, Iterator, Sequence
from types import TracebackType
from typing import TYPE_CHECKING, Any, TextIO

import httpx

if TYPE_CHECKING:
    from ojarvis.shell.prose import Prose

LEVEL_NAMES = {1: "lecture", 2: "action", 3: "destruction"}

#: Lignes de sortie montrées sous une commande (Jarvis, lui, lit tout).
PREVIEW_LINES = 12

#: Lignes d'un diff montrées avant validation.
DIFF_LINES = 80

#: Largeur des cartes et des lignes alignées à droite : lisible même sur un grand écran.
CARD_WIDTH = 84
#: Hors d'un vrai terminal (un tube, les tests), la largeur ne se mesure pas : celle-ci.
PLAIN_WIDTH = 80


class Style:
    """Couleurs ANSI, seulement dans un vrai terminal et sans `NO_COLOR`."""

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def __call__(self, text: str, *codes: str) -> str:
        if not self.enabled or not codes:
            return text
        return f"\033[{';'.join(codes)}m{text}\033[0m"

    def prompt(self, text: str, *codes: str) -> str:
        """Pour `input()` : readline ne doit pas compter les codes de couleur."""
        if not self.enabled:
            return text
        return f"\001\033[{';'.join(codes)}m\002{text}\001\033[0m\002"


# Les couleurs du terminal (16 de base) : chaque thème, clair ou sombre, les ajuste au fond.
DIM, BOLD, RED, GREEN, YELLOW, CYAN, WHITE = "2", "1", "31", "32", "33", "36", "97"
MAGENTA, BLACK = "35", "30"

LEVEL_COLORS = {1: GREEN, 2: YELLOW, 3: RED}
#: Le fond du badge d'une carte, selon le niveau (texte noir dessus).
LEVEL_BADGES = {1: "42", 2: "43", 3: "41"}


def seconds(value: float) -> str:
    """« 0,2 s » : une durée à la française."""
    return f"{value:.1f}".replace(".", ",") + " s"


class Spinner:
    """« Jarvis réfléchit… 4 s » pendant l'attente, effacé dès que quelque chose s'affiche."""

    FRAMES: Sequence[str] = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    #: Pendant l'écoute (« /voix ») : une onde qui défile.
    WAVE: Sequence[str] = tuple(
        "".join("▁▂▃▄▅▆▇█▇▆▅▄▃▂"[(i + k) % 14] for k in range(10)) for i in range(14)
    )

    def __init__(self, out: TextIO, enabled: bool, style: Style | None = None) -> None:
        self._out = out
        self._enabled = enabled
        self._style = style or Style(False)
        self._label = ""
        self._frames: Sequence[str] = self.FRAMES
        self._since = 0.0
        self._halt = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self, label: str = "Jarvis réfléchit…", frames: Sequence[str] | None = None) -> None:
        if not self._enabled or self._thread is not None:
            return
        self._label = label
        self._frames = frames or self.FRAMES
        self._since = time.monotonic()
        self._halt.clear()
        self._thread = threading.Thread(target=self._spin, name="spinner", daemon=True)
        self._thread.start()

    def _spin(self) -> None:
        for frame in itertools.cycle(self._frames):
            if self._halt.wait(0.1):
                return
            elapsed = int(time.monotonic() - self._since)
            label = f"{self._label}  {elapsed} s" if elapsed >= 1 else self._label
            self._out.write(f"\r  {self._style(frame, BOLD, CYAN)} {self._style(label, DIM)}")
            self._out.flush()

    def stop(self) -> None:
        if self._thread is None:
            return
        self._halt.set()
        self._thread.join()
        self._thread = None
        self._out.write("\r\033[K")
        self._out.flush()

    def __enter__(self) -> Spinner:
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        trace: TracebackType | None,
    ) -> None:
        self.stop()


def sse_events(lines: Iterable[str]) -> Iterator[tuple[str, dict[str, Any]]]:
    """Les événements d'un flux SSE (`event: …` puis `data: {…}`)."""
    kind: str | None = None
    for line in lines:
        if line.startswith("event: "):
            kind = line[7:].strip()
        elif line.startswith("data: ") and kind is not None:
            data = json.loads(line[6:])
            yield kind, data if isinstance(data, dict) else {"value": data}
            kind = None


def error_message(response: httpx.Response) -> str:
    try:
        error = response.json().get("error", {})
        message = error.get("message") if isinstance(error, dict) else None
    except ValueError:
        message = None
    return str(message or f"Réponse inattendue du moteur (HTTP {response.status_code}).")


def preview(report: str, lines: int = PREVIEW_LINES) -> str:
    """La fin de ce qu'a affiché une commande, sans l'en-tête déjà montré (la commande, le
    dossier) ; le code de sortie seulement s'il signale un échec."""
    body = [
        line
        for line in report.splitlines()
        if not line.startswith(("$ ", "dossier : ", "code de sortie 0 ", "--- sortie ---"))
    ]
    kept = body[-lines:]
    hidden = len(body) - len(kept)
    return "\n".join(([f"[… {hidden} lignes plus haut]"] if hidden > 0 else []) + kept)


def _last_output_line(result: str) -> str:
    """La dernière ligne de la sortie d'une commande (« /dev/sda2 457G 377G 58G 87% / »)."""
    output = result.split("--- sortie ---", 1)[-1]
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    return lines[-1] if lines else "(aucune sortie)"


class Terminal:
    """Les lignes du terminal, en couleurs si possible ; une ligne laissée ouverte (la roue
    d'attente, une réponse en cours) est refermée avant la suivante."""

    _out: TextIO
    _style: Style
    _line_open: bool
    #: La dernière ligne écrite était vide : pas d'autre ligne vide à la suite.
    _last_blank: bool = True
    #: La réponse en cours, coupée aux mots ; refermée avant toute autre ligne.
    _prose: Prose | None = None
    #: La dernière ligne écrite était l'en-tête « ◆ Jarvis » : la réponse suit sans ligne vide.
    _after_header: bool = False

    def _write(self, text: str) -> None:
        self._out.write(text)
        self._out.flush()
        if text:
            self._line_open = not text.endswith("\n")
            self._last_blank = False

    def line(self, text: str, *, header: bool = False) -> None:
        self._end_prose()
        if self._line_open:
            self._write("\n")
        self._write(text + "\n")
        self._last_blank = not text.strip()
        self._after_header = header

    def gap(self) -> None:
        """Une ligne vide, sauf juste après une autre."""
        self._end_prose()
        if not self._last_blank:
            self.line("")

    def _end_prose(self) -> None:
        if self._prose is not None:
            prose, self._prose = self._prose, None
            prose.close()

    def width(self) -> int:
        """La largeur d'une carte : celle du terminal, sans dépasser `CARD_WIDTH`."""
        if not self._style.enabled:
            return PLAIN_WIDTH
        columns = shutil.get_terminal_size((PLAIN_WIDTH, 24)).columns
        return max(40, min(columns - 1, CARD_WIDTH))

    def _aligned(self, left: tuple[str, str], right: tuple[str, str]) -> str:
        """Deux textes (brut, coloré) aux deux bords de la carte."""
        room = self.width() - len(left[0]) - len(right[0])
        return left[1] + " " * max(2, room) + right[1]

    def _rail(self, text: str, color: str, *codes: str) -> None:
        """Des lignes sous un rail, en retrait (la sortie d'une commande)."""
        for line in text.splitlines():
            self.line(self._style("    │ ", color) + self._style(line, *codes))

    def _card_top(self, title: str, level: int) -> None:
        """« ╭─ À VALIDER · NIVEAU 2 · ACTION ─────── » : le haut d'une carte, son badge."""
        color = LEVEL_COLORS.get(level, YELLOW)
        badge = f" {title} "
        fill = max(3, self.width() - 5 - len(badge) - 1)
        self.line(
            self._style("  ╭─ ", color)
            + self._style(badge, BOLD, BLACK, LEVEL_BADGES.get(level, "43"))
            + self._style(" " + "─" * fill, color)
        )

    def _card(self, text: str, color: str) -> None:
        """Une ligne dans la carte, derrière son rail."""
        self.line(self._style("  │ ", color) + text)

    def error(self, message: str) -> None:
        self.line(self._style(f"  ✗ {message}", BOLD, RED))
