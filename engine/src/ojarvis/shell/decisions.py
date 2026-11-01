"""Tes décisions dans le terminal : un plan à valider (diff, avis du relecteur, capture d'un
clic), « toujours pour cette session », sudo que tu lances toi-même, et le suivi du plan
étape par étape jusqu'à ses mesures avant / après.
"""

from __future__ import annotations

import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, TextIO

import httpx

from ojarvis.agent.measures import MEASURE_AFTER, MEASURE_BEFORE
from ojarvis.shell.display import (
    BOLD,
    CYAN,
    DIFF_LINES,
    DIM,
    GREEN,
    LEVEL_COLORS,
    LEVEL_NAMES,
    RED,
    YELLOW,
    Spinner,
    Terminal,
    _last_output_line,
    error_message,
    preview,
)
from ojarvis.shell.signatures import signatures
from ojarvis.tools.base import Level

YES = frozenset({"o", "oui", "y", "yes"})

#: « [e]xécuter moi-même » : une commande sudo que tu lances.
RUN_MYSELF = frozenset({"e", "exécuter", "executer", "o", "oui"})

ALWAYS = frozenset({"t", "toujours"})

#: La fin de la sortie d'une commande lancée par toi, rendue à Jarvis (la limite de l'API).
RAN_OUTPUT_CHARS = 20_000

#: Lance une commande proposée (dans ce dossier) : son code de sortie et sa sortie.
Runner = Callable[[str, str], tuple[int, str]]


def run_yourself(command: str, folder: str, out: TextIO) -> tuple[int, str]:
    """Lance la commande dans ce terminal, comme si tu la tapais : sudo lit ton mot de passe
    sur le terminal lui-même, jamais sur un tube, et Jarvis ne le voit pas. La sortie
    s'affiche au fil de l'eau ; sa fin revient à Jarvis."""
    cwd = Path(folder).expanduser()
    process = subprocess.Popen(  # noqa: S603 - ta commande, montrée puis lancée par toi
        ["/bin/bash", "-c", command],
        cwd=cwd if cwd.is_dir() else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
    )
    kept: list[str] = []
    size = 0
    if process.stdout is not None:
        try:
            for line in process.stdout:
                out.write(line)
                out.flush()
                kept.append(line)
                size += len(line)
                while size > RAN_OUTPUT_CHARS and len(kept) > 1:
                    size -= len(kept.pop(0))
        except KeyboardInterrupt:
            process.terminate()
    return process.wait(), "".join(kept)[-RAN_OUTPUT_CHARS:]


class PlanDecisions(Terminal):
    """Le plan proposé, ta décision, puis son suivi : la part du terminal qui touche aux
    validations (mélangée à `ShellClient`)."""

    http: httpx.Client
    folder: str
    home: Path
    allowed: set[str]
    marks_dir: Path | None
    _ask: Callable[[str], str]
    _run: Runner
    _proposals: list[dict[str, Any]]
    _spinner: Spinner
    _poll_s: float
    _wait_s: float
    #: Le temps passé à décider, dans cette demande : il ne compte pas dans sa durée.
    _waited: float = 0.0

    def _question(self, prompt: str) -> str:
        """Ta réponse à une question ; une entrée fermée (Ctrl+D) vaut un refus."""
        asked = time.monotonic()
        try:
            return self._ask(prompt).strip().lower()
        except EOFError:
            return ""
        finally:
            self._waited += time.monotonic() - asked

    def _bottom(self, color: str, *parts: tuple[str, ...]) -> str:
        """« ╰─ Valider ?  o oui · n non  › » : le bas d'une carte, la question (sûre pour
        readline : les couleurs n'y comptent pas dans la largeur)."""
        return self._style.prompt("  ╰─ ", color) + "".join(
            self._style.prompt(text, *codes) for text, *codes in parts
        )

    def _decide(self, plan: dict[str, Any]) -> str | None:
        """Montre le plan et ta décision ; rend son id s'il a tourné jusqu'au bout."""
        plan_id = str(plan["id"])
        level = int(plan.get("max_level", 2))
        if plan.get("status") != "proposed":
            return self._wait(plan_id)  # une lecture : partie sans attendre
        color = LEVEL_COLORS.get(level, YELLOW)
        self.gap()
        name = LEVEL_NAMES.get(level, "action").upper()
        self._card_top(f"À VALIDER · NIVEAU {level} · {name}", level)
        clicks = False
        for step in plan.get("steps", []):
            self._step_line(step, color)
            if step.get("preview"):
                self._diff(str(step["preview"]), color)
            if step.get("repere"):
                clicks = True
                self._capture(str(step["repere"]), color)
        if clicks:
            self._card(
                "Après ta validation, mets la fenêtre visée devant : Jarvis clique dès qu'il "
                "revoit l'élément (15 s au plus).",
                color,
            )
        if isinstance(plan.get("review"), dict):
            self._review(plan["review"], color)
        if plan.get("web"):
            # Ici presque tout suit une lecture de ta machine ; seul le web mérite l'alerte.
            warning = "⚠ L'idée vient d'un contenu lu sur le web : vérifie bien."
            self._card(self._style(warning, YELLOW), color)
        always = self._always(plan)
        if always and always <= self.allowed:
            kinds = ", ".join(sorted(always))
            self.line(
                self._style("  ╰─ ", color)
                + self._style(f"Validé d'office (toujours pour cette session : {kinds})", color)
            )
            approved = True
        else:
            approved = self._ask_approval(level, always, color)
        self._line_open = False
        if not approved:
            self.http.post(f"/v1/plans/{plan_id}/refuse")
            self.line(self._style("  Refusé : rien n'a été lancé.", DIM))
            return None
        response = self.http.post(
            f"/v1/plans/{plan_id}/approve", json={"double_confirm": level >= 3}
        )
        if response.status_code != 200:
            self.error(error_message(response))
            return None
        return self._wait(plan_id)

    def _step_line(self, step: dict[str, Any], color: str) -> None:
        """« │ 1  pytest -q                    mesure avant » : une étape du plan."""
        number = str(step["position"])
        description = str(step["description"])
        left = (
            f"  │ {number}  {description}",
            self._style("  │ ", color) + self._style(number, BOLD) + f"  {description}",
        )
        if not step.get("measure"):
            self.line(left[1])
            return
        tag = f"mesure {step['measure']}"
        self.line(self._aligned(left, (tag, self._style(tag, DIM))))

    def _capture(self, mark: str, frame: str) -> None:
        """Où Jarvis va cliquer : la capture annotée, à ouvrir d'un Ctrl+clic."""
        if self.marks_dir is None or not mark.isalnum():
            return
        path = self.marks_dir / f"{mark}.jpg"
        if path.is_file():
            where = f"   où Jarvis va cliquer : {path.as_uri()} (Ctrl+clic)"
            self._card(self._style(where, DIM), frame)

    def _diff(self, diff: str, frame: str) -> None:
        """Le diff d'une étape, en couleurs : + ajouté, - retiré."""
        lines = diff.splitlines()
        shown_lines = lines[:DIFF_LINES]
        for line in shown_lines:
            if line.startswith(("+++", "---")):
                color = BOLD
            elif line.startswith("+"):
                color = GREEN
            elif line.startswith("-"):
                color = RED
            elif line.startswith("@@"):
                color = CYAN
            else:
                color = DIM
            self._card("   " + self._style(line, color), frame)
        if len(lines) > len(shown_lines):
            more = f"   [… {len(lines) - len(shown_lines)} lignes de plus]"
            self._card(self._style(more, DIM), frame)

    def _review(self, review: dict[str, Any], frame: str) -> None:
        """L'avis du relecteur (un autre modèle) sur les modifications proposées."""
        ok = review.get("ok")
        if ok is True:
            mark, color = "✓", GREEN
        elif ok is False:
            mark, color = "⚠", RED
        else:
            mark, color = "?", YELLOW
        notes = str(review.get("notes") or ("rien à signaler" if ok else ""))
        who = f"{review.get('provider')} / {review.get('model')}"
        self._card(self._style(f"{mark} Relecture · {who} : {notes}", color), frame)

    def _always(self, plan: dict[str, Any]) -> frozenset[str] | None:
        """Les types de commande du plan si « toujours » peut s'y appliquer : seulement des
        commandes de niveau 2, aucune retouche de fichier, rien après une lecture du web."""
        if int(plan.get("max_level", 2)) != Level.AGIR or plan.get("web"):
            return None
        kinds: set[str] = set()
        for step in plan.get("steps", []):
            if step.get("tool") != "executer_commande" or not step.get("command"):
                return None
            found = signatures(str(step["command"]), cwd=Path(self.folder), home=self.home)
            if found is None:
                return None
            kinds |= found
        return frozenset(kinds) or None

    def _admin(self) -> dict[str, Any] | None:
        """La commande sudo proposée pendant ce tour : tu la lances toi-même, en un appui. sudo
        te demande ton mot de passe directement ; Jarvis ne le voit jamais, et lit la sortie."""
        proposals, self._proposals = self._proposals, []
        if not proposals:
            return None
        proposal = proposals[-1]
        command = str(proposal.get("command", ""))
        level = int(proposal.get("level", Level.AGIR))
        color = RED if level >= Level.DETRUIRE else YELLOW
        self._spinner.stop()
        self.gap()
        self._card_top("COMMANDE D'ADMINISTRATEUR · C'EST TOI QUI LA LANCES", level)
        self._card(self._style(command, BOLD), color)
        if proposal.get("why"):
            self._card(self._style(str(proposal["why"]), DIM), color)
        if level >= Level.DETRUIRE:
            question = self._bottom(
                color,
                ("Tape « oui » pour la lancer toi-même", BOLD),
                (", autre chose pour refuser  › ", DIM),
            )
        else:
            question = self._bottom(
                color,
                ("Lancer ?", BOLD),
                ("  ",),
                ("e", BOLD),
                (" exécuter moi-même · ", DIM),
                ("n", BOLD),
                (" non  › ", DIM),
            )
        answer = self._question(question)
        accepted = answer == "oui" if level >= Level.DETRUIRE else answer in RUN_MYSELF
        if not accepted:
            self.line(self._style("  Pas lancée.", DIM))
            return None
        self.line(
            self._style(
                "  ↳ sudo te demande ton mot de passe ici : il va directement à sudo, Jarvis ne "
                "le voit pas.",
                DIM,
            )
        )
        code, output = self._run(command, self.folder)
        self.line(self._style(f"  · code de sortie {code}", DIM))
        return {"command": command, "code": code, "output": output}

    def _ask_approval(self, level: int, always: frozenset[str] | None, color: str) -> bool:
        if level >= Level.DETRUIRE:
            question = self._bottom(
                color,
                ("Tape « oui » en entier pour confirmer", BOLD),
                (", autre chose pour refuser  › ", DIM),
            )
        else:
            choices: list[tuple[str, ...]] = [("o", BOLD), (" oui · ", DIM), ("n", BOLD)]
            choices.append((" non", DIM) if not always else (" non · ", DIM))
            if always:
                kinds = ", ".join(sorted(always))
                choices += [("t", BOLD), (f" toujours ({kinds})", DIM)]
            question = self._bottom(color, ("Valider ?", BOLD), ("  ",), *choices, ("  › ", DIM))
        answer = self._question(question)
        if level >= Level.DETRUIRE:
            return answer == "oui"
        if always and answer in ALWAYS:
            self.allowed |= always
            return True
        return answer in YES

    def _wait(self, plan_id: str) -> str | None:
        """Suit le plan étape par étape, jusqu'à sa fin."""
        shown_steps: set[int] = set()
        deadline = time.monotonic() + self._wait_s
        self.gap()
        self._spinner.start("Jarvis exécute…")
        try:
            while time.monotonic() < deadline:
                plan = self.http.get(f"/v1/plans/{plan_id}").json()
                for step in plan.get("steps", []):
                    position = int(step["position"])
                    if position in shown_steps or step["status"] in ("pending", "running"):
                        continue
                    shown_steps.add(position)
                    self._spinner.stop()
                    self._show_step(step)
                    self._spinner.start("Jarvis exécute…")
                if plan.get("status") in ("done", "failed", "refused", "stopped"):
                    self._spinner.stop()
                    self._measures(plan)
                    return plan_id
                time.sleep(self._poll_s)
        finally:
            self._spinner.stop()
        self.error("Le plan tourne encore dans Jarvis ; je n'attends plus.")
        return None

    def _measures(self, plan: dict[str, Any]) -> None:
        """La mesure prise avant les actions, puis après : la preuve, côte à côte."""
        found = {
            str(step.get("why")): _last_output_line(str(step.get("result") or ""))
            for step in plan.get("steps", [])
            if step.get("status") == "done"
        }
        before, after = found.get(MEASURE_BEFORE), found.get(MEASURE_AFTER)
        if before is not None and after is not None:
            self.line(self._style("  mesure avant  ", DIM) + before)
            self.line(self._style("  mesure après  ", DIM) + self._style(after, GREEN))

    def _show_step(self, step: dict[str, Any]) -> None:
        if step["status"] == "done":
            self.line(self._style("  ✓ ", GREEN) + str(step["description"]))
            if step.get("tool") == "executer_commande" and step.get("result"):
                self._rail(preview(str(step["result"])), DIM, DIM)
        elif step["status"] == "failed":
            self.line(self._style(f"  ✗ {step['description']} : {step.get('error')}", RED))
        else:
            self.line(self._style(f"  – {step['description']} (non lancée)", DIM))
