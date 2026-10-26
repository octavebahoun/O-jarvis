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
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from pydantic import Field

from ojarvis.core.errors import NotFoundError
from ojarvis.shell.commands import classify
from ojarvis.tools.base import Level, Tool, ToolArgs, ToolContext, ToolOutput
from ojarvis.tools.guards import resolve_user_path, shown

#: Gardés de la sortie : le début (souvent l'essentiel) et la fin (l'erreur d'une compilation).
HEAD_BYTES = 4_000
TAIL_BYTES = 8_000
#: Après la fin de la commande, le temps laissé aux tubes pour se vider : un processus lancé
#: en arrière-plan qui les garderait ouverts est alors arrêté.
DRAIN_S = 2.0
POLL_S = 0.05
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


def clean_environment(environ: Mapping[str, str]) -> dict[str, str]:
    """L'environnement du moteur, sans ses secrets ni ses variables à lui."""
    env = {
        name: value
        for name, value in environ.items()
        if not SECRET_NAME.search(name) and not name.startswith(("OJARVIS_", "VITE_OJARVIS"))
    }
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
    # `process.wait()` attend aussi la fermeture des tubes : un processus laissé en
    # arrière-plan (« serveur & ») la retarderait jusqu'au délai. Le code de sortie, lui,
    # est connu dès que la commande se termine.
    timed_out = not await _exited(process, timeout_s)
    if timed_out:
        _kill_group(process)
        await _exited(process, DRAIN_S)
    _done, pending = await asyncio.wait(readers, timeout=DRAIN_S)
    if pending:
        _kill_group(process)  # un processus resté en arrière-plan garde les tubes ouverts
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
    )


def report(command: str, folder: str, result: CommandResult, timeout_s: float) -> str:
    """Ce que le modèle lit : la commande, son issue, puis ses sorties."""
    if result.timed_out:
        status = f"arrêtée après {timeout_s:g} s (délai dépassé)"
    else:
        status = f"code de sortie {result.code} ({result.duration_s:g} s)"
    lines = [f"$ {command}", f"dossier : {folder}", status]
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


class RunCommand(Tool[CommandArgs]):
    name: ClassVar[str] = "executer_commande"
    description: ClassVar[str] = (
        "Lance une commande dans un terminal, dans le dossier de la session, et rend sa "
        "sortie. Le niveau dépend de la commande : lecture (ls, git status, df…) = 1, autre "
        "action = 2, suppression ou arrêt (rm, kill, docker prune…) = 3. Jamais sudo : donne "
        "la commande à l'utilisateur pour qu'il la tape. Pas de commande interactive (elle "
        "ne reçoit aucune entrée)."
    )
    Args: ClassVar[type[ToolArgs]] = CommandArgs
    level: ClassVar[Level] = Level.AGIR
    shell_only: ClassVar[bool] = True

    def __init__(self, home: Path, *, environ: Mapping[str, str] | None = None) -> None:
        self._home = home
        self._environ = environ

    def _folder(self, args: CommandArgs) -> Path:
        return resolve_user_path(args.dossier, self._home)

    def level_for(self, args: CommandArgs) -> Level:
        return classify(args.commande, cwd=self._folder(args), home=self._home).level

    def describe(self, args: CommandArgs) -> str:
        folder = shown(self._folder(args), self._home)
        return f"Lancer « {args.commande} » dans {folder}"

    async def run(self, args: CommandArgs, ctx: ToolContext) -> ToolOutput:
        folder = self._folder(args)
        if not folder.is_dir():
            raise NotFoundError(f"Dossier introuvable : {args.dossier}.")
        env = clean_environment(os.environ if self._environ is None else self._environ)
        result = await run_command(args.commande, folder, timeout_s=args.delai_s, env=env)
        where = shown(folder, ctx.home)
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

#: Terminaux connus, avec la façon de leur faire lancer une commande. `xdg-terminal-exec`
#: d'abord : c'est le terminal choisi par l'utilisateur (Ubuntu 26.04 : Ptyxis).
TERMINALS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("xdg-terminal-exec", ()),
    ("ptyxis", ("--new-window", "--")),
    ("gnome-terminal", ("--",)),
    ("kgx", ("--",)),
    ("konsole", ("-e",)),
    ("xfce4-terminal", ("-x",)),
    ("x-terminal-emulator", ("-e",)),
    ("xterm", ("-e",)),
)
#: Ce dont le terminal a besoin pour rejoindre le même moteur (mêmes données, même connexion).
#: Passé par `env` : un terminal déjà ouvert lance la commande avec son propre environnement.
SHELL_ENV = (
    "OJARVIS_HOME",
    "OJARVIS_USER_HOME",
    "XDG_DATA_HOME",
    "XDG_CONFIG_HOME",
    "XDG_CACHE_HOME",
    "XDG_STATE_HOME",
    "XDG_RUNTIME_DIR",
)


def shell_command(folder: Path, environ: Mapping[str, str]) -> list[str]:
    """La commande qui lance `ojarvis-shell` dans ce dossier, avec ce même Python."""
    variables = [f"{name}={environ[name]}" for name in SHELL_ENV if environ.get(name)]
    program = [sys.executable, "-m", "ojarvis.shell.cli", "--dossier", str(folder)]
    return ["env", *variables, *program] if variables else program


def terminal_command(
    folder: Path, environ: Mapping[str, str], which: Callable[[str], str | None]
) -> tuple[str, list[str]] | None:
    """Le premier terminal installé, et la ligne qui y ouvre le mode shell."""
    for name, prefix in TERMINALS:
        path = which(name)
        if path is not None:
            return name, [path, *prefix, *shell_command(folder, environ)]
    return None


def _launch(command: list[str]) -> None:
    subprocess.Popen(  # noqa: S603 - un terminal installé, avec ojarvis-shell
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,  # le terminal vit sa vie, même si le moteur s'arrête
    )


class OpenShellArgs(ToolArgs):
    dossier: str = Field(
        default="~",
        min_length=1,
        max_length=1000,
        description="Le dossier où travailler (~/… ; ~ par défaut).",
    )


class OpenShell(Tool[OpenShellArgs]):
    name: ClassVar[str] = "ouvrir_mode_shell"
    description: ClassVar[str] = (
        "Ouvre le mode shell : un terminal où tu travailles avec l'utilisateur dans un dossier "
        "(lancer des commandes, lire leur sortie, corriger). À utiliser quand il dit « passe en "
        "mode shell » ou veut que tu lances des commandes."
    )
    Args: ClassVar[type[ToolArgs]] = OpenShellArgs
    level: ClassVar[Level] = Level.LIRE

    def __init__(
        self,
        home: Path,
        *,
        environ: Mapping[str, str] | None = None,
        which: Callable[[str], str | None] = shutil.which,
        launch: Callable[[list[str]], None] = _launch,
    ) -> None:
        self._home = home
        self._environ = environ
        self._which = which
        self._launch = launch

    def describe(self, args: OpenShellArgs) -> str:
        return f"Ouvrir le mode shell dans {args.dossier}"

    async def run(self, args: OpenShellArgs, ctx: ToolContext) -> ToolOutput:
        folder = resolve_user_path(args.dossier, self._home)
        if not folder.is_dir():
            raise NotFoundError(f"Dossier introuvable : {args.dossier}.")
        environ = os.environ if self._environ is None else self._environ
        found = terminal_command(folder, environ, self._which)
        if found is None:
            raise NotFoundError(
                "Aucun terminal trouvé. Lance « ojarvis-shell » toi-même dans un terminal."
            )
        name, command = found
        self._launch(command)
        where = shown(folder, ctx.home)
        return ToolOutput(
            f"Mode shell ouvert dans {where} ({name}) : Jarvis t'y attend.",
            {"dossier": str(folder), "terminal": name},
        )
