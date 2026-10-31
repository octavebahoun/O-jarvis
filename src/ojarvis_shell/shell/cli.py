"""`ojarvis-shell` : Jarvis dans ton terminal, dans le dossier où tu le lances.

Il rejoint le moteur de l'app s'il tourne (`ojarvis.shell.connection`), sinon il lance le
sien, qui s'arrête avec lui. Chaque message part sur le canal « shell » avec le dossier
courant : une lecture s'exécute aussitôt, une action s'affiche et attend ton « o », une
destruction ton « oui » en toutes lettres. Après ta décision, Jarvis relit le résultat et
continue.
"""

from __future__ import annotations

import argparse
import contextlib
import itertools
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any, TextIO

import httpx

from ojarvis import __version__
from ojarvis.agent.measures import MEASURE_AFTER, MEASURE_BEFORE
from ojarvis.config import resolve_paths
from ojarvis.shell.commands import signatures
from ojarvis.shell.connection import (
    EXIT_LOCKED,
    READY_PREFIX,
    EngineInfo,
    info_path,
    python_module,
    read_info,
)
from ojarvis.tools.base import Level
from ojarvis.tools.guards import BlockedTargetError, resolve_user_path, shown

#: Ce que Jarvis dit après une validation : la suite part avec les résultats du plan.
CONTINUE = "Continue."
YES = frozenset({"o", "oui", "y", "yes"})
#: « [e]xécuter moi-même » : une commande sudo que tu lances.
RUN_MYSELF = frozenset({"e", "exécuter", "executer", "o", "oui"})
ALWAYS = frozenset({"t", "toujours"})
#: Pour quitter : ces mots ne partent jamais vers le modèle.
EXIT_WORDS = frozenset({"exit", "quit", ":q", "/exit", "/quit", "/quitter"})
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


DIM, BOLD, RED, GREEN, YELLOW, CYAN = "2", "1", "31", "32", "33", "36"
LEVEL_COLORS = {1: GREEN, 2: YELLOW, 3: RED}


class Spinner:
    """« Jarvis réfléchit… » pendant l'attente, effacé dès que quelque chose s'affiche."""

    FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self, out: TextIO, enabled: bool) -> None:
        self._out = out
        self._enabled = enabled
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
            self._out.write(f"\r{frame} {self._label}")
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


@dataclass(frozen=True, slots=True)
class Budget:
    """Ce qu'une demande peut consommer en suites automatiques, après tes validations."""

    follow_ups: int = 12
    seconds: float = 20 * 60
    dollars: float = 1.0


#: Une phrase dite (« /voix ») : le temps laissé pour parler.
DICTATION_S = 30
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


def _last_output_line(result: str) -> str:
    """La dernière ligne de la sortie d'une commande (« /dev/sda2 457G 377G 58G 87% / »)."""
    output = result.split("--- sortie ---", 1)[-1]
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    return lines[-1] if lines else "(aucune sortie)"


class ShellClient:
    """Une session : la conversation, et la boucle « proposer, valider, continuer »."""

    def __init__(
        self,
        http: httpx.Client,
        folder: str,
        *,
        out: TextIO,
        ask: Callable[[str], str],
        style: Style,
        poll_s: float = 0.3,
        wait_s: float = 1800.0,
        budget: Budget | None = None,
        home: Path | None = None,
        run: Runner | None = None,
        own_engine: bool = False,
        marks_dir: Path | None = None,
    ) -> None:
        self.http = http
        self.folder = folder
        #: Moteur lancé pour ce terminal (l'app est fermée) : il n'a ni micro ni voix.
        self.own_engine = own_engine
        #: Les captures annotées des éléments trouvés à l'écran (#48), pour un clic à valider.
        self.marks_dir = marks_dir
        self.conversation_id: str | None = None
        #: Plans finis dont le modèle n'a pas encore lu les résultats (arrêt sur le budget) :
        #: ils partent avec ton prochain message, comme une commande que tu as lancée.
        self.pending: list[str] = []
        self.pending_ran: dict[str, Any] | None = None
        #: Commandes sudo proposées pendant le tour : c'est toi qui les lances.
        self._proposals: list[dict[str, Any]] = []
        self._run: Runner = run or (lambda command, where: run_yourself(command, where, out))
        self._out = out
        self._ask = ask
        self._style = style
        self._poll_s = poll_s
        self._wait_s = wait_s
        self._spinner = Spinner(out, style.enabled)
        self._line_open = False
        #: Le modèle déjà annoncé pendant ce tour (il ne change qu'en cas de relais).
        self._route: str | None = None
        self.budget = budget or Budget()
        self._spent = 0.0
        self.home = home or Path.home()
        #: Les types de commande validés « toujours » : seulement pour cette session.
        self.allowed: set[str] = set()

    # ------------------------------------------------------------------ affichage

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
        self.line(self._style(f"✗ {message}", RED))

    # ------------------------------------------------------------------ un message

    def dictate(self) -> str | None:
        """« /voix » (#10) : une phrase dite, transcrite par le Jarvis de l'app, montrée ici,
        puis traitée comme si tu l'avais écrite. Comme le bouton micro : rien ne quitte la
        machine avant que tu parles."""
        self.line(self._style("◉ Je t'écoute : parle, puis marque une pause.", CYAN))
        self._spinner.start("J'écoute…")
        try:
            response = self.http.post(
                "/v1/voice/dictate", json={"timeout_s": DICTATION_S}, timeout=DICTATION_S + 15
            )
        except httpx.HTTPError as exc:
            self.error(f"Le moteur ne répond plus ({type(exc).__name__}).")
            return None
        finally:
            self._spinner.stop()
        if response.status_code == 409 and self.own_engine:
            self.error(
                "La voix vit dans l'app, et elle est fermée : quitte ce terminal (exit), ouvre "
                "l'app, puis relance ojarvis-shell."
            )
            return None
        if response.status_code != 200:
            self.error(error_message(response))
            return None
        heard = str(response.json().get("text") or "").strip()
        if not heard:
            self.line(self._style("Je n'ai rien entendu.", DIM))
            return None
        self.line(self._style("Tu as dit : ", DIM) + f"« {heard} »")
        return heard

    def send(self, text: str, *, spoken: bool = False) -> None:
        """Un message, puis chaque plan qu'il propose : décidé, attendu, et la suite, dans le
        budget de la demande. Au-delà, il s'arrête proprement et attend ton « continue ».
        `spoken` : la demande a été dite, la dernière réponse l'est aussi."""
        self._spent = 0.0
        started = time.monotonic()
        follow_ups = 0
        try:
            pending, self.pending = self.pending, []
            ran, self.pending_ran = self.pending_ran, None
            plans = self._turn(text, after_plan=pending, ran=ran)
            while True:
                finished = [plan_id for plan in plans if (plan_id := self._decide(plan))]
                ran = self._admin()
                if not finished and ran is None:
                    return
                reason = self._over_budget(follow_ups, time.monotonic() - started)
                if reason is not None:
                    message = (
                        f"Budget de cette demande atteint ({reason}) : je m'arrête ici. "
                        "Écris « continue » pour reprendre."
                    )
                    self.line(self._style(message, YELLOW))
                    self.pending, self.pending_ran = finished, ran
                    return
                follow_ups += 1
                plans = self._turn(CONTINUE, after_plan=finished, ran=ran)
        finally:
            if self._spent > 0:
                self.line(self._style(f"· coût de cette demande : {self._spent:.3f} $", DIM))
            if spoken:
                self._speak("".join(self._reply))

    def _speak(self, reply: str) -> None:
        """La réponse dite à voix haute (sans les marques du terminal), si la voix est en veille."""
        text = re.sub(r"[`*_#]", "", reply).strip()
        if text:
            with contextlib.suppress(httpx.HTTPError):
                self.http.post("/v1/voice/say", json={"text": text[:2000]})

    def _over_budget(self, follow_ups: int, elapsed_s: float) -> str | None:
        if follow_ups >= self.budget.follow_ups:
            return f"{follow_ups} suites"
        if elapsed_s >= self.budget.seconds:
            return f"{elapsed_s / 60:.0f} min"
        if self._spent >= self.budget.dollars:
            return f"{self._spent:.2f} $"
        return None

    def _turn(
        self,
        text: str,
        *,
        after_plan: list[str] | None = None,
        ran: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        body: dict[str, Any] = {"text": text, "channel": "shell", "folder": self.folder}
        if self.conversation_id is not None:
            body["conversation_id"] = self.conversation_id
        if after_plan:
            body["after_plan"] = after_plan
        if ran is not None:
            body["ran"] = ran
        plans: list[dict[str, Any]] = []
        self._route = None
        self._reply: list[str] = []
        self._spinner.start()
        try:
            timeout = httpx.Timeout(15.0, read=None)
            with self.http.stream("POST", "/v1/chat", json=body, timeout=timeout) as response:
                if response.status_code != 200:
                    response.read()
                    self._spinner.stop()
                    self.error(error_message(response))
                    return []
                for kind, data in sse_events(response.iter_lines()):
                    self._on_event(kind, data, plans)
        finally:
            self._spinner.stop()
        if self._line_open:
            self._write("\n")
        return plans

    def _on_event(self, kind: str, data: dict[str, Any], plans: list[dict[str, Any]]) -> None:
        if kind == "start":
            self.conversation_id = str(data["conversation_id"])
        elif kind == "route":
            route = f"{data.get('provider')} / {data.get('model')}"
            if route != self._route:
                self._route = route
                relay = f", relais de {data['fallback_from']}" if data.get("fallback_from") else ""
                self._spinner.stop()
                if data.get("relay"):
                    self.line(self._style(f"↑ {data['relay']}", YELLOW))
                self.line(self._style(f"· {route}{relay}", DIM))
                self._spinner.start()
        elif kind == "delta":
            self._spinner.stop()
            text = str(data.get("text", ""))
            if not self._line_open and text.startswith("\n"):
                text = "\n" + text.lstrip("\n")  # une seule ligne vide après une commande
            self._write(text)
            self._reply.append(text)
        elif kind == "tool":
            self._on_tool(data)
        elif kind == "notice":
            self._spinner.stop()
            self.line(self._style(f"· {data.get('text', '')}", DIM))
            self._spinner.start()
        elif kind == "plan":
            plans.append(data)
        elif kind == "error":
            self._spinner.stop()
            self.error(str(data.get("message", "erreur du moteur")))
        elif kind == "done":
            self._spent += float(data.get("cost_usd") or 0.0)

    def _on_tool(self, data: dict[str, Any]) -> None:
        status = data.get("status")
        if status == "running" or data.get("name") == "proposer_un_plan":
            return  # le plan s'affiche à part, avec la question
        self._spinner.stop()
        description = str(data.get("description") or data.get("name"))
        if status == "failed":
            self.line(self._style(f"  ✗ {description} : {data.get('error')}", RED))
        elif status == "done":
            if data.get("sudo"):
                self._proposals.append(dict(data["sudo"]))  # montrée à la fin du tour
            else:
                self.line(self._style(f"  ✓ {description}", DIM))
            if data.get("output") and not data.get("sudo"):
                self._indented(preview(str(data["output"])), DIM)
        self._spinner.start()

    # ------------------------------------------------------------------ un plan

    def _decide(self, plan: dict[str, Any]) -> str | None:
        """Montre le plan et ta décision ; rend son id s'il a tourné jusqu'au bout."""
        plan_id = str(plan["id"])
        level = int(plan.get("max_level", 2))
        if plan.get("status") != "proposed":
            return self._wait(plan_id)  # une lecture : partie sans attendre
        color = LEVEL_COLORS.get(level, YELLOW)
        self.line(self._style(f"┌ À valider · niveau {level} ({LEVEL_NAMES.get(level)})", color))
        clicks = False
        for step in plan.get("steps", []):
            measure = f"(mesure {step['measure']}) " if step.get("measure") else ""
            self.line(
                self._style("│ ", color) + f"{step['position']}. {measure}{step['description']}"
            )
            if step.get("preview"):
                self._diff(str(step["preview"]), color)
            if step.get("repere"):
                clicks = True
                self._capture(str(step["repere"]), color)
        if clicks:
            self.line(
                self._style("│ ", color)
                + "Après ta validation, mets la fenêtre visée devant : Jarvis clique dès qu'il "
                "revoit l'élément (15 s au plus)."
            )
        if isinstance(plan.get("review"), dict):
            self._review(plan["review"], color)
        if plan.get("web"):
            # Ici presque tout suit une lecture de ta machine ; seul le web mérite l'alerte.
            warning = "│ ⚠ l'idée vient d'un contenu lu sur le web : vérifie bien."
            self.line(self._style(warning, color))
        always = self._always(plan)
        if always and always <= self.allowed:
            kinds = ", ".join(sorted(always))
            self.line(
                self._style(f"└ Validé d'office (toujours pour cette session : {kinds})", color)
            )
            approved = True
        else:
            approved = self._ask_approval(level, always, color)
        self._line_open = False
        if not approved:
            self.http.post(f"/v1/plans/{plan_id}/refuse")
            self.line(self._style("Refusé : rien n'a été lancé.", DIM))
            return None
        response = self.http.post(
            f"/v1/plans/{plan_id}/approve", json={"double_confirm": level >= 3}
        )
        if response.status_code != 200:
            self.error(error_message(response))
            return None
        return self._wait(plan_id)

    def _capture(self, mark: str, frame: str) -> None:
        """Où Jarvis va cliquer : la capture annotée, à ouvrir d'un Ctrl+clic."""
        if self.marks_dir is None or not mark.isalnum():
            return
        path = self.marks_dir / f"{mark}.jpg"
        if path.is_file():
            self.line(
                self._style("│   ", frame)
                + self._style(f"où Jarvis va cliquer : {path.as_uri()} (Ctrl+clic)", DIM)
            )

    def share(self) -> None:
        """« /partage » : partager l'écran en direct avec Jarvis, ou arrêter."""
        try:
            state = self.http.get("/v1/desktop/share").json()
            if state.get("active"):
                response = self.http.post("/v1/desktop/share/stop")
                message = "Partage de l'écran arrêté."
            else:
                self._spinner.start("GNOME te demande ton accord (la première fois)…")
                try:
                    response = self.http.post("/v1/desktop/share/start", timeout=120)
                finally:
                    self._spinner.stop()
                message = (
                    "Écran partagé en direct : Jarvis le voit sans capture à chaque fois. GNOME "
                    "affiche son indicateur ; « /partage » pour arrêter."
                )
        except httpx.HTTPError as exc:
            self.error(f"Le moteur ne répond plus ({type(exc).__name__}).")
            return
        if response.status_code != 200:
            self.error(error_message(response))
            return
        self.line(self._style(message, DIM))

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
            self.line(self._style("│   ", frame) + self._style(line, color))
        if len(lines) > len(shown_lines):
            more = f"│   [… {len(lines) - len(shown_lines)} lignes de plus]"
            self.line(self._style(more, DIM))

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
        verdict = self._style(f"Relecture · {who} : {mark} {notes}", color)
        self.line(self._style("│ ", frame) + verdict)

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
        self.line(self._style("┌ Commande d'administrateur : c'est toi qui la lances", color))
        self.line(self._style("│ ", color) + command)
        if proposal.get("why"):
            self.line(self._style("│ ", color) + self._style(str(proposal["why"]), DIM))
        if level >= Level.DETRUIRE:
            question = (
                "└ Destruction : tape « oui » pour la lancer toi-même, autre chose pour refuser › "
            )
        else:
            question = "└ [e]xécuter moi-même / [n]on › "
        try:
            answer = self._ask(self._style.prompt(question, color)).strip().lower()
        except EOFError:
            answer = ""  # entrée fermée (Ctrl+D) : un refus
        accepted = answer == "oui" if level >= Level.DETRUIRE else answer in RUN_MYSELF
        if not accepted:
            self.line(self._style("Pas lancée.", DIM))
            return None
        self.line(
            self._style(
                "↳ sudo te demande ton mot de passe ici : il va directement à sudo, Jarvis ne "
                "le voit pas.",
                DIM,
            )
        )
        code, output = self._run(command, self.folder)
        self.line(self._style(f"· code de sortie {code}", DIM))
        return {"command": command, "code": code, "output": output}

    def _ask_approval(self, level: int, always: frozenset[str] | None, color: str) -> bool:
        if level >= Level.DETRUIRE:
            question = "└ Destruction : tape « oui » pour confirmer, autre chose pour refuser › "
        elif always:
            kinds = ", ".join(sorted(always))
            question = f"└ Valider ? [o]ui / [n]on / [t]oujours ({kinds}) › "
        else:
            question = "└ Valider ? [o]ui / [n]on › "
        try:
            answer = self._ask(self._style.prompt(question, color)).strip().lower()
        except EOFError:
            answer = ""  # entrée fermée (Ctrl+D) : un refus, rien n'est lancé
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
            self.line(self._style(f"· Avant : {before}", DIM))
            self.line(self._style(f"· Après : {after}", DIM))

    def _show_step(self, step: dict[str, Any]) -> None:
        if step["status"] == "done":
            self.line(self._style(f"  ✓ {step['description']}", GREEN))
            if step.get("tool") == "executer_commande" and step.get("result"):
                self._indented(preview(str(step["result"])), DIM)
        elif step["status"] == "failed":
            self.line(self._style(f"  ✗ {step['description']} : {step.get('error')}", RED))
        else:
            self.line(self._style(f"  – {step['description']} (non lancée)", DIM))


# --------------------------------------------------------------------------- le moteur


class EngineUnavailableError(Exception):
    pass


def reachable(info: EngineInfo) -> bool:
    try:
        response = httpx.get(
            f"{info.url}/v1/status",
            headers={"Authorization": f"Bearer {info.token}"},
            timeout=3.0,
        )
    except httpx.HTTPError:
        return False
    return response.status_code == 200


def start_engine(
    env: Mapping[str, str], *, timeout_s: float = 180.0
) -> tuple[EngineInfo, subprocess.Popen[bytes]]:
    """Lance un moteur pour ce terminal, comme le fait l'app : jeton sur l'entrée standard,
    gardée ouverte ; il s'arrête quand le terminal se ferme."""
    token = secrets.token_urlsafe(32)
    process = subprocess.Popen(  # noqa: S603 - notre propre moteur, sans entrée extérieure
        python_module("ojarvis"),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env={**env, "OJARVIS_SKIP_WARMUP": "1"},
        start_new_session=True,  # Ctrl+C dans le terminal ne coupe pas le moteur
    )
    stdin, stdout = process.stdin, process.stdout
    if stdin is None or stdout is None:
        process.kill()
        raise EngineUnavailableError("Le moteur n'a pas de tubes d'entrée et de sortie.")
    stdin.write(token.encode() + b"\n")
    stdin.flush()
    ready: dict[str, Any] = {}

    def read_ready() -> None:
        for raw in stdout:
            line = raw.decode("utf-8", "replace")
            if line.startswith(READY_PREFIX) and not ready:
                ready.update(json.loads(line[len(READY_PREFIX) :]))
            # La suite est lue et jetée : un tube plein bloquerait le moteur.

    threading.Thread(target=read_ready, name="engine-stdout", daemon=True).start()
    deadline = time.monotonic() + timeout_s
    while not ready:
        if process.poll() is not None:
            if process.returncode == EXIT_LOCKED:
                raise EngineUnavailableError(
                    "Le moteur de l'app tourne déjà mais ne publie pas sa connexion : "
                    "redémarre l'app (version à jour) ou ferme-la, puis relance."
                )
            raise EngineUnavailableError(
                f"Le moteur n'a pas démarré (code {process.returncode}) : voir son journal, "
                "~/.local/state/o-jarvis/logs/engine.log."
            )
        if time.monotonic() > deadline:
            process.kill()
            raise EngineUnavailableError("Le moteur met trop de temps à démarrer.")
        time.sleep(0.1)
    info = EngineInfo(int(ready["port"]), int(ready["pid"]), token, str(ready.get("version")))
    return info, process


def connect(env: Mapping[str, str]) -> tuple[EngineInfo, subprocess.Popen[bytes] | None]:
    """Le moteur de l'app s'il tourne, sinon un moteur à ce terminal."""
    path = info_path(resolve_paths(env), env)
    if path is not None:
        info = read_info(path)
        if info is not None and reachable(info):
            return info, None
    return start_engine(env)


def stop_engine(process: subprocess.Popen[bytes] | None) -> None:
    if process is None:
        return
    if process.stdin is not None:
        with contextlib.suppress(OSError):
            process.stdin.close()  # le moteur s'arrête à la fermeture de son entrée
    try:
        process.wait(15)
    except subprocess.TimeoutExpired:
        process.kill()


# --------------------------------------------------------------------------- terminal


def _history(env: Mapping[str, str]) -> Path:
    return resolve_paths(env).state_dir / "shell_history"


def _setup_readline(path: Path) -> Callable[[], None]:
    try:
        import readline  # noqa: PLC0415 - absent hors des terminaux Unix
    except ImportError:
        return lambda: None
    with contextlib.suppress(OSError):
        readline.read_history_file(path)
    readline.set_history_length(1000)

    def save() -> None:
        with contextlib.suppress(OSError):
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            readline.write_history_file(path)
            path.chmod(0o600)

    return save


def _banner(client: ShellClient, style: Style, where: str, own_engine: bool) -> None:
    profile = ""
    with contextlib.suppress(httpx.HTTPError, ValueError):
        if client.http.get("/v1/settings").json().get("model_profile") == "meta":
            profile = " · profil Tout Meta"
    origin = "moteur lancé pour ce terminal" if own_engine else "le Jarvis de ton app"
    client.line(style(f"O-jarvis · mode shell (v{__version__})", BOLD, CYAN))
    client.line(style(f"Dossier {where} · {origin}{profile}", DIM))
    client.line(
        style(
            "« /voix » : parler · « /partage » : écran en direct · « /nouveau » : nouvelle "
            "conversation · « /toujours » : ce qui est validé d'office · « exit » : quitter",
            DIM,
        )
    )


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="ojarvis-shell", description="Jarvis dans ton terminal, dans un dossier à toi."
    )
    parser.add_argument(
        "--dossier",
        help="le dossier de travail (défaut : le dossier courant) ; l'app s'en sert quand elle "
        "ouvre le mode shell, car un terminal déjà lancé ignore le dossier de l'appelant",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    env = dict(os.environ)
    out = sys.stdout
    style = Style(out.isatty() and "NO_COLOR" not in env)
    home = Path(env.get("OJARVIS_USER_HOME") or Path.home())
    try:
        folder = resolve_user_path(args.dossier or str(Path.cwd()), home)
        if not folder.is_dir():
            raise BlockedTargetError(f"Dossier introuvable : {args.dossier}.")
    except BlockedTargetError as exc:
        out.write(style(f"✗ {exc.message} Lance ojarvis-shell depuis un dossier à toi.\n", RED))
        return 2
    spinner = Spinner(out, style.enabled)
    spinner.start("Je rejoins Jarvis…")
    try:
        info, process = connect(env)
    except EngineUnavailableError as exc:
        spinner.stop()
        out.write(style(f"✗ {exc}\n", RED))
        return 1
    spinner.stop()
    save_history = _setup_readline(_history(env))
    headers = {"Authorization": f"Bearer {info.token}"}
    with httpx.Client(base_url=info.url, headers=headers, timeout=30.0) as http:
        client = ShellClient(
            http,
            str(folder),
            out=out,
            ask=input,
            style=style,
            home=home,
            own_engine=process is not None,
            marks_dir=resolve_paths(env).state_dir / "desktop" / "reperes",
        )
        where = shown(folder, home)
        _banner(client, style, where, client.own_engine)
        try:
            _loop(client, style, where)
        finally:
            save_history()
            stop_engine(process)
    return 0


def _loop(client: ShellClient, style: Style, where: str) -> None:
    while True:
        try:
            text = input("\n" + style.prompt(f"{where} ❯ ", CYAN)).strip()
        except EOFError:
            client.line("")
            return
        except KeyboardInterrupt:
            continue
        if not text:
            continue
        if text in EXIT_WORDS:
            return
        if _command(client, style, text):
            continue
        _ask(client, style, text)


def _command(client: ShellClient, style: Style, text: str) -> bool:
    """Les commandes « / » du terminal ; faux pour une demande à Jarvis."""
    if text in ("/voix", "/v"):
        heard = client.dictate()
        if heard:
            _ask(client, style, heard, spoken=True)
    elif text == "/partage":
        client.share()
    elif text == "/nouveau":
        client.conversation_id = None
        client.pending.clear()
        client.line(style("Nouvelle conversation.", DIM))
    elif text == "/toujours":
        kinds = ", ".join(sorted(client.allowed)) or "rien pour l'instant"
        client.line(style(f"Validé d'office dans cette session : {kinds}", DIM))
    elif text == "/oublier":
        client.allowed.clear()
        client.line(style("Chaque commande demandera de nouveau ta validation.", DIM))
    else:
        return False
    return True


def _ask(client: ShellClient, style: Style, text: str, *, spoken: bool = False) -> None:
    try:
        client.send(text, spoken=spoken)
    except KeyboardInterrupt:
        client.line(style("(interrompu)", DIM))
    except httpx.HTTPError as exc:
        client.error(f"Le moteur ne répond plus ({type(exc).__name__}).")


if __name__ == "__main__":
    sys.exit(main())
