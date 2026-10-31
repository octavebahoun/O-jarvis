"""Ce que le terminal affiche : couleurs, roue d'attente, aperçus de sortie, flux SSE du moteur."""

from __future__ import annotations

import itertools
import json
import threading
from collections.abc import Iterable, Iterator
from types import TracebackType
from typing import Any, TextIO

import httpx

LEVEL_NAMES = {1: "lecture", 2: "action", 3: "destruction"}

#: Lignes de sortie montrées sous une commande (Jarvis, lui, lit tout).
PREVIEW_LINES = 12

#: Lignes d'un diff montrées avant validation.
DIFF_LINES = 80


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


DIM, BOLD, RED, GREEN, YELLOW, CYAN, WHITE = "2", "1", "31", "32", "33", "36", "97"

LEVEL_COLORS = {1: GREEN, 2: YELLOW, 3: RED}


class Spinner:
    """« Jarvis réfléchit… » pendant l'attente, effacé dès que quelque chose s'affiche."""

    FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self, out: TextIO, enabled: bool, style: Style | None = None) -> None:
        self._out = out
        self._enabled = enabled
        self._style = style or Style(False)
        self._label = ""
        self._halt = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self, label: str = "Jarvis réfléchit…") -> None:
        if not self._enabled or self._thread is not None:
            return
        self._label = label
        self._halt.clear()
        self._thread = threading.Thread(target=self._spin, name="spinner", daemon=True)
        self._thread.start()

    def _spin(self) -> None:
        for frame in itertools.cycle(self.FRAMES):
            if self._halt.wait(0.1):
                return
            self._out.write(f"\r{self._style(frame, BOLD, CYAN)} {self._style(self._label, DIM)}")
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
    d'attente) est refermée avant la suivante."""

    _out: TextIO
    _style: Style
    _line_open: bool

    def _write(self, text: str) -> None:
        self._out.write(text)
        self._out.flush()
        if text:
            self._line_open = not text.endswith("\n")

    def line(self, text: str) -> None:
        if self._line_open:
            self._write("\n")
        self._write(text + "\n")

    def _indented(self, text: str, *codes: str) -> None:
        for line in text.splitlines():
            self.line(self._style(f"    {line}", *codes))

    def error(self, message: str) -> None:
        self.line(self._style(f"✗ {message}", BOLD, RED))
