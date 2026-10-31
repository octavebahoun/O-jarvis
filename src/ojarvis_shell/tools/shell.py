"""Mode shell : lancer une commande dans un dossier et rendre sa sortie.

Le niveau dépend de la commande (`ojarvis.shell.commands`) : une lecture part tout de suite,
une action attend ta validation, une destruction ta double confirmation. L'outil n'est
proposé au modèle qu'en mode shell : ouvrir le mode shell est ton accord pour cet outil.

- `bash -c` dans le dossier de la session, sans entrée : une commande qui pose une question
  échoue au lieu de bloquer. Au délai, tout son groupe de processus est arrêté.
- Les variables d'environnement secrètes (clés, jetons, mots de passe, jeton du moteur) sont
  retirées avant le lancement.
- La sortie est tronquée (début et fin gardés) et rendue comme une donnée, jamais comme une
  instruction ; les secrets connus sont masqués avant tout envoi au modèle.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import signal
import subprocess
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

import anyio.to_thread
from pydantic import Field

from ojarvis.config import resolve_paths
from ojarvis.core.errors import NotFoundError
from ojarvis.shell.commands import classify
from ojarvis.shell.servers import BackgroundSessions
from ojarvis.tools.base import Level, Tool, ToolArgs, ToolContext, ToolOutput
from ojarvis.tools.guards import resolve_user_path, shown

#: Gardés de la sortie : le début (souvent l'essentiel) et la fin (l'erreur d'une compilation).
HEAD_BYTES = 4_000
TAIL_BYTES = 8_000
#: Après la fin de la commande, le temps laissé aux tubes pour se vider : un processus lancé
#: en arrière-plan qui les garderait ouverts est alors arrêté.
DRAIN_S = 2.0
POLL_S = 0.05
#: Arrière-plan : le temps laissé à une commande avant le premier coup d'œil à son journal.
BACKGROUND_CHECK_S = 2.0
SECRET_NAME = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|COOKIE", re.IGNORECASE)
#: Rendu stable et sans attente : ni couleur, ni pager, ni question.
QUIET_ENV = {
    "PAGER": "cat",
    "GIT_PAGER": "cat",
    "GIT_TERMINAL_PROMPT": "0",
    "DEBIAN_FRONTEND": "noninteractive",
    "NO_COLOR": "1",
    "TERM": "dumb",
}


def _engine_bin() -> str | None:
    """Le dossier `bin` de l'environnement Python du moteur, s'il tourne dans un venv."""
    return str(Path(sys.prefix) / "bin") if sys.prefix != sys.base_prefix else None


def clean_environment(
    environ: Mapping[str, str], *, engine_bin: str | None = None
) -> dict[str, str]:
    """L'environnement du moteur, sans ses secrets, ses variables à lui, ni son Python.

    Une commande doit utiliser les outils de ta machine : lancé par `uv run`, le moteur met
    son venv en tête du PATH, et un `pip install` aurait installé dans Jarvis lui-même (vu le
    27/10/2026 : `python3 -m pytest` tournait avec le Python interne de Jarvis)."""
    own = engine_bin if engine_bin is not None else _engine_bin()
    env = {
        name: value
        for name, value in environ.items()
        if not SECRET_NAME.search(name)
        and not name.startswith(("OJARVIS_", "VITE_OJARVIS"))
        and name not in ("VIRTUAL_ENV", "PYTHONHOME")
    }
    if own and "PATH" in env:
        kept = [entry for entry in env["PATH"].split(os.pathsep) if entry and entry != own]
        env["PATH"] = os.pathsep.join(kept)
    env.update(QUIET_ENV)
    return env


class _Capture:
    """Le début et la fin d'un flux, sans tout garder en mémoire."""

    def __init__(self) -> None:
        self.head = bytearray()
        self.tail = bytearray()
        self.total = 0

    def add(self, chunk: bytes) -> None:
        self.total += len(chunk)
        room = HEAD_BYTES - len(self.head)
        if room > 0:
            self.head += chunk[:room]
            chunk = chunk[room:]
        if chunk:
            self.tail += chunk
            if len(self.tail) > TAIL_BYTES:
                del self.tail[: len(self.tail) - TAIL_BYTES]

    def text(self) -> str:
        cut = self.total - len(self.head) - len(self.tail)
        head = self.head.decode("utf-8", "replace")
        tail = self.tail.decode("utf-8", "replace")
        if cut <= 0:
            return (head + tail).rstrip()
        return f"{head}\n[… {cut} octets coupés …]\n{tail}".rstrip()


async def _pump(stream: asyncio.StreamReader | None, capture: _Capture) -> None:
    if stream is None:
        return
    while chunk := await stream.read(65_536):
        capture.add(chunk)


async def _exited(process: asyncio.subprocess.Process, timeout_s: float) -> bool:
    deadline = time.monotonic() + timeout_s
    while process.returncode is None:
        if time.monotonic() >= deadline:
            return False
        await asyncio.sleep(POLL_S)
    return True


def _kill_group(process: asyncio.subprocess.Process) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(process.pid, signal.SIGKILL)


@dataclass(frozen=True, slots=True)
class CommandResult:
    code: int | None
    stdout: str
    stderr: str
    duration_s: float
    timed_out: bool
    #: La commande a fini, mais un processus lancé en arrière-plan sans redirection gardait
    #: sa sortie ouverte : arrêté au délai.
    stray_stopped: bool = False


async def run_command(
    command: str, cwd: Path, *, timeout_s: float, env: Mapping[str, str]
) -> CommandResult:
    start = time.monotonic()
    process = await asyncio.create_subprocess_exec(
        "bash",
        "-c",
        command,
        cwd=cwd,
        env=dict(env),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,  # son propre groupe : arrêté d'un coup au délai
    )
    out, err = _Capture(), _Capture()
    readers = {
        asyncio.create_task(_pump(process.stdout, out)),
        asyncio.create_task(_pump(process.stderr, err)),
    }
    # `process.wait()` attend aussi la fermeture des tubes ; le code de sortie, lui, est connu
    # dès que la commande se termine.
    deadline = start + timeout_s
    timed_out = not await _exited(process, timeout_s)
    if timed_out:
        _kill_group(process)
        await _exited(process, DRAIN_S)
    # La sortie se ferme quand tout ce qui l'a reçue s'est terminé. Un serveur bien détaché
    # (sortie redirigée vers un fichier) ne la garde pas : il continue de tourner. Un processus
    # laissé en arrière-plan sans redirection la garde : on l'attend jusqu'au délai, pas plus.
    # (Avant le 27/10/2026, il était arrêté après 2 s : un moteur occupé tuait ainsi un
    # serveur Vite pourtant bien détaché.)
    remaining = DRAIN_S if timed_out else max(deadline - time.monotonic(), 0.0)
    _done, pending = await asyncio.wait(readers, timeout=remaining)
    stray = bool(pending) and not timed_out
    if pending:
        _kill_group(process)
        for reader in pending:
            reader.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(process.wait(), DRAIN_S)
    return CommandResult(
        code=None if timed_out else process.returncode,
        stdout=out.text(),
        stderr=err.text(),
        duration_s=round(time.monotonic() - start, 2),
        timed_out=timed_out,
        stray_stopped=stray,
    )


@dataclass(frozen=True, slots=True)
class BackgroundResult:
    pid: int
    log: Path
    #: None : toujours en cours après le premier coup d'œil ; sinon son code de sortie.
    code: int | None
    head: str


def _spawn_detached(
    command: str, cwd: Path, env: dict[str, str], log_dir: Path
) -> tuple[subprocess.Popen[bytes], Path]:
    log_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", command.lower()).strip("-")[:40] or "commande"
    log = log_dir / f"{time.strftime('%Y%m%d-%H%M%S')}-{slug}.log"
    descriptor = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        process = subprocess.Popen(  # noqa: S603 - une commande validée (ou une lecture)
            ["bash", "-c", command],  # noqa: S607 - bash du système, comme au premier plan
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=descriptor,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    finally:
        os.close(descriptor)
    return process, log


def _read_head(log: Path) -> str:
    return log.read_bytes()[: HEAD_BYTES + TAIL_BYTES].decode("utf-8", "replace").rstrip()


async def start_background(
    command: str, cwd: Path, *, env: Mapping[str, str], log_dir: Path
) -> BackgroundResult:
    """Une commande qui ne se termine pas (serveur) : détachée, sortie dans un journal.

    Elle a sa propre session : ni le délai, ni l'arrêt du moteur ne l'arrêtent. Un coup d'œil
    après quelques secondes montre une erreur immédiate (port déjà pris, script absent)."""
    process, log = await anyio.to_thread.run_sync(_spawn_detached, command, cwd, dict(env), log_dir)
    await asyncio.sleep(BACKGROUND_CHECK_S)
    head = await anyio.to_thread.run_sync(_read_head, log)
    return BackgroundResult(process.pid, log, process.poll(), head)


def background_report(command: str, folder: str, result: BackgroundResult, log: str) -> str:
    if result.code is None:
        status = f"lancée en arrière-plan : PID {result.pid}, journal {log}"
    else:
        status = f"arrêtée tout de suite (code de sortie {result.code}) ; journal {log}"
    lines = [f"$ {command}", f"dossier : {folder}", status]
    lines += [f"--- début du journal ({BACKGROUND_CHECK_S:g} s) ---", result.head or "(vide)"]
    return "\n".join(lines)


def report(command: str, folder: str, result: CommandResult, timeout_s: float) -> str:
    """Ce que le modèle lit : la commande, son issue, puis ses sorties."""
    if result.timed_out:
        status = f"arrêtée après {timeout_s:g} s (délai dépassé)"
    else:
        status = f"code de sortie {result.code} ({result.duration_s:g} s)"
    lines = [f"$ {command}", f"dossier : {folder}", status]
    if result.stray_stopped:
        lines.append(
            "un processus laissé en arrière-plan gardait la sortie ouverte : arrêté au délai. "
            "Pour un serveur, relance avec arriere_plan=true."
        )
    if result.stdout:
        lines += ["--- sortie ---", result.stdout]
    if result.stderr:
        lines += ["--- erreurs ---", result.stderr]
    if not result.stdout and not result.stderr:
        lines.append("(aucune sortie)")
    return "\n".join(lines)


class CommandArgs(ToolArgs):
    commande: str = Field(
        min_length=1,
        max_length=4000,
        description="La ligne de commande bash, telle que tu la taperais dans un terminal.",
    )
    dossier: str = Field(
        min_length=1,
        max_length=1000,
        description="Le dossier où la lancer : celui de la session (chemin absolu ou ~/…).",
    )
    delai_s: int = Field(
        default=60,
        ge=1,
        le=900,
        description="Durée maximale en secondes (une installation peut demander plusieurs "
        "minutes).",
    )
    arriere_plan: bool = Field(
        default=False,
        description="Pour une commande qui ne se termine pas (serveur de développement, "
        "surveillance) : lancée détachée, sa sortie va dans un journal ; tu reçois son PID et "
        "le début du journal.",
    )


class RunCommand(Tool[CommandArgs]):
    name: ClassVar[str] = "executer_commande"
    description: ClassVar[str] = (
        "Lance une commande dans un terminal, dans le dossier de la session, et rend sa "
        "sortie. Le niveau dépend de la commande : lecture (ls, git status, df…) = 1, autre "
        "action = 2, suppression ou arrêt (rm, kill, docker prune…) = 3. Jamais sudo : donne "
        "la commande à l'utilisateur pour qu'il la tape. Pas de commande interactive (elle "
        "ne reçoit aucune entrée). Un serveur : arriere_plan=true."
    )
    Args: ClassVar[type[ToolArgs]] = CommandArgs
    level: ClassVar[Level] = Level.AGIR
    shell_only: ClassVar[bool] = True

    def __init__(
        self,
        home: Path,
        *,
        log_dir: Path | None = None,
        environ: Mapping[str, str] | None = None,
        sessions: BackgroundSessions | None = None,
    ) -> None:
        self._home = home
        #: Les serveurs qu'il lance : voir_page regarde leurs pages sans redemander.
        self._sessions = sessions
        #: Journaux des commandes lancées en arrière-plan (lisibles par une commande).
        self._log_dir = log_dir or resolve_paths().state_dir / "shell"
        self._environ = environ

    def _folder(self, args: CommandArgs) -> Path:
        return resolve_user_path(args.dossier, self._home)

    def level_for(self, args: CommandArgs) -> Level:
        return classify(args.commande, cwd=self._folder(args), home=self._home).level

    def describe(self, args: CommandArgs) -> str:
        folder = shown(self._folder(args), self._home)
        how = "Lancer en arrière-plan" if args.arriere_plan else "Lancer"
        return f"{how} « {args.commande} » dans {folder}"

    async def run(self, args: CommandArgs, ctx: ToolContext) -> ToolOutput:
        folder = self._folder(args)
        if not folder.is_dir():
            raise NotFoundError(f"Dossier introuvable : {args.dossier}.")
        env = clean_environment(os.environ if self._environ is None else self._environ)
        where = shown(folder, ctx.home)
        if args.arriere_plan:
            started = await start_background(args.commande, folder, env=env, log_dir=self._log_dir)
            if self._sessions is not None:
                self._sessions.add(started.pid)
            log = shown(started.log, ctx.home)
            return ToolOutput(
                background_report(args.commande, where, started, log),
                {"pid": started.pid, "journal": str(started.log), "code": started.code},
                external=True,
                source="commande",
            )
        result = await run_command(args.commande, folder, timeout_s=args.delai_s, env=env)
        return ToolOutput(
            report(args.commande, where, result, args.delai_s),
            {
                "code": result.code,
                "dossier": str(folder),
                "duree_s": result.duration_s,
                "delai_depasse": result.timed_out,
            },
            external=True,
            source="commande",
        )


# --------------------------------------------------------------------------- ouvrir le mode shell


# --------------------------------------------------------------------------- sudo, lancé par toi


# --------------------------------------------------------------------------- voir une page locale
