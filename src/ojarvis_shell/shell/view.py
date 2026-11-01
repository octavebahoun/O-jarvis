"""Un tour dans le terminal : « ◆ Jarvis » et son modèle, chaque lecture avec son niveau et sa
durée, la réponse en retrait coupée aux mots, puis sa durée et son coût."""

from __future__ import annotations

import time
from typing import Any

from ojarvis.shell.display import (
    BOLD,
    CYAN,
    DIM,
    GREEN,
    LEVEL_NAMES,
    RED,
    YELLOW,
    Spinner,
    Terminal,
    preview,
    seconds,
)
from ojarvis.shell.prose import Prose, prose_width


class TurnView(Terminal):
    """Ce que le moteur raconte pendant un tour, mis en page (mélangé à `ShellClient`)."""

    _spinner: Spinner
    _proposals: list[dict[str, Any]]
    _started: dict[str, float]
    _reply: list[str]
    _spent: float

    def _footer(self, elapsed_s: float) -> None:
        """« 9,4 s · 0,006 $ » sous la réponse : sa durée (sans tes temps de décision) et son
        coût."""
        self._end_prose()
        cost = f" · {self._spent:.3f} $".replace(".", ",") if self._spent > 0 else ""
        self.line(self._style(f"  {seconds(elapsed_s)}{cost}", DIM))

    def _announce(self, data: dict[str, Any], *, first: bool) -> None:
        """« ◆ Jarvis   meta / muse-spark-1.3 » en tête de la demande ; un relais en cours de
        route est signalé à sa place."""
        relay = f", relais de {data['fallback_from']}" if data.get("fallback_from") else ""
        model = (
            self._style(f"{data.get('provider')} / ", DIM)
            + self._style(str(data.get("model")), CYAN)
            + (self._style(relay, DIM) if relay else "")
        )
        if first:
            self.line("")
        if data.get("relay"):
            self.line(self._style(f"  ↑ {data['relay']}", YELLOW))
        if first:
            self.line(self._style("◆ Jarvis", BOLD, CYAN) + "   " + model, header=True)
        else:
            self.line(self._style("  · ", DIM) + model)

    def _on_delta(self, text: str) -> None:
        self._spinner.stop()
        self._reply.append(text)
        if self._prose is None:
            if not text.strip():
                return  # les blancs avant le premier mot
            if not self._after_header:
                self.gap()  # une ligne vide après une commande ou un plan
            self._prose = Prose(self._write, self._style, prose_width(self._style))
        self._prose.feed(text)

    def _on_tool(self, data: dict[str, Any]) -> None:
        status = data.get("status")
        if data.get("name") == "proposer_un_plan":
            return  # le plan s'affiche à part, avec la question
        if status == "running":
            self._started[str(data.get("id"))] = time.monotonic()
            return
        self._spinner.stop()
        description = str(data.get("description") or data.get("name"))
        if status == "failed":
            self.line(self._style(f"  ✗ {description} : {data.get('error')}", RED))
        elif status == "done":
            if data.get("sudo"):
                self._proposals.append(dict(data["sudo"]))  # montrée à la fin du tour
            else:
                self.line(self._done_line(data, description))
            if data.get("output") and not data.get("sudo"):
                self._rail(preview(str(data["output"])), DIM, DIM)
        self._spinner.start()

    def _done_line(self, data: dict[str, Any], description: str) -> str:
        """« ✓ df -h /                          lecture · 0,2 s » : la lecture, son niveau et
        sa durée au bord de la carte."""
        facts = []
        if data.get("level") in LEVEL_NAMES:
            facts.append(LEVEL_NAMES[int(data["level"])])
        began = self._started.pop(str(data.get("id")), None)
        if began is not None:
            facts.append(seconds(time.monotonic() - began))
        left = (f"  ✓ {description}", self._style("  ✓ ", GREEN) + description)
        if not facts:
            return left[1]
        right = " · ".join(facts)
        return self._aligned(left, (right, self._style(right, DIM)))
