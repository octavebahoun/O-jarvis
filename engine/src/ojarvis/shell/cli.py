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
import os
import re
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

import httpx

from ojarvis import __version__
from ojarvis.config import resolve_paths
from ojarvis.shell.connection import (
    EngineUnavailableError,
    connect,
    stop_engine,
)
from ojarvis.shell.decisions import PlanDecisions, Runner, run_yourself
from ojarvis.shell.display import (
    BOLD,
    CYAN,
    DIM,
    GREEN,
    RED,
    WHITE,
    YELLOW,
    Spinner,
    Style,
    error_message,
    preview,
    sse_events,
)
from ojarvis.tools.guards import BlockedTargetError, resolve_user_path, shown

#: Ce que Jarvis dit après une validation : la suite part avec les résultats du plan.
CONTINUE = "Continue."
#: Pour quitter : ces mots ne partent jamais vers le modèle.
EXIT_WORDS = frozenset({"exit", "quit", ":q", "/exit", "/quit", "/quitter"})


@dataclass(frozen=True, slots=True)
class Budget:
    """Ce qu'une demande peut consommer en suites automatiques, après tes validations."""

    follow_ups: int = 12
    seconds: float = 20 * 60
    dollars: float = 1.0


#: Une phrase dite (« /voix ») : le temps laissé pour parler.
DICTATION_S = 30


class ShellClient(PlanDecisions):
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
        #: Moteur lancé par ce terminal (l'app était fermée) : complet, l'app peut le rejoindre.
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
        self._spinner = Spinner(out, style.enabled, style)
        self._line_open = False
        #: Le modèle déjà annoncé pendant ce tour (il ne change qu'en cas de relais).
        self._route: str | None = None
        self.budget = budget or Budget()
        self._spent = 0.0
        self.home = home or Path.home()
        #: Les types de commande validés « toujours » : seulement pour cette session.
        self.allowed: set[str] = set()

    # ------------------------------------------------------------------ affichage

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
                self.line(
                    self._style(f"· {data.get('provider')} / ", DIM)
                    + self._style(str(data.get("model")), CYAN)
                    + (self._style(relay, DIM) if relay else "")
                )
                self._spinner.start()
        elif kind == "delta":
            self._spinner.stop()
            text = str(data.get("text", ""))
            if not self._line_open and text.startswith("\n"):
                text = "\n" + text.lstrip("\n")  # une seule ligne vide après une commande
            if not self._reply:
                self._write(self._style("◆ ", BOLD, CYAN))
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
                self.line(self._style("  ✓ ", GREEN) + description)
            if data.get("output") and not data.get("sudo"):
                self._indented(preview(str(data["output"])), DIM)
        self._spinner.start()

    # ------------------------------------------------------------------ un plan

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


# --------------------------------------------------------------------------- le moteur


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
    client.line(style("◉ O-jarvis", BOLD, WHITE) + style(f" · mode shell (v{__version__})", CYAN))
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
    spinner = Spinner(out, style.enabled, style)
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
            if process is not None:
                _warn_app(client, style)
            stop_engine(process)
    return 0


def _warn_app(client: ShellClient, style: Style) -> None:
    """Le moteur de ce terminal s'arrête avec lui : l'app qui l'avait rejoint relance le sien."""
    with contextlib.suppress(httpx.HTTPError, ValueError):
        if client.http.get("/v1/status").json().get("stream_clients", 0) > 0:
            client.line(
                style("L'app utilisait ce moteur : elle relance le sien (quelques secondes).", DIM)
            )


def _loop(client: ShellClient, style: Style, where: str) -> None:
    while True:
        try:
            text = input(
                "\n" + style.prompt(f"{where} ", DIM) + style.prompt("❯ ", BOLD, CYAN)
            ).strip()
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
