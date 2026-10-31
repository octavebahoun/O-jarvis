"""« Passe en mode shell » : ouvrir ton propre terminal sur `ojarvis-shell`, dans le dossier
demandé, une fois (le modèle ne rouvre pas un terminal déjà ouvert à l'instant).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import ClassVar

from pydantic import Field

from ojarvis.core.errors import NotFoundError
from ojarvis.shell.connection import python_module
from ojarvis.tools.base import Level, Tool, ToolArgs, ToolContext, ToolOutput
from ojarvis.tools.guards import resolve_user_path, shown

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
    program = [*python_module("ojarvis.shell.cli", frozen_as="shell"), "--dossier", str(folder)]
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


#: Un terminal ouvert il y a moins de 5 minutes dans le même dossier n'est pas rouvert : le
#: 27/10/2026, chaque phrase qui suivait « passe en mode shell » en rouvrait un.
RECENT_OPEN_S = 300.0


class OpenShellArgs(ToolArgs):
    dossier: str = Field(
        default="~",
        min_length=1,
        max_length=1000,
        description="Le dossier où travailler (~/… ; ~ par défaut).",
    )
    rouvrir: bool = Field(
        default=False,
        description="Rouvrir un terminal qui vient d'être ouvert ici : seulement si "
        "l'utilisateur dit l'avoir fermé et le redemande.",
    )


class OpenShell(Tool[OpenShellArgs]):
    name: ClassVar[str] = "ouvrir_mode_shell"
    description: ClassVar[str] = (
        "Ouvre le mode shell : un terminal où l'utilisateur écrit ses demandes et où tu lances "
        "les commandes, dans un dossier. Seulement quand il demande le mode shell ou un "
        "terminal, et une seule fois : déjà ouvert, il ne se rouvre pas."
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
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._home = home
        self._environ = environ
        self._which = which
        self._launch = launch
        self._clock = clock
        #: Quand un terminal a été ouvert, par dossier.
        self._opened: dict[Path, float] = {}

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
        where = shown(folder, ctx.home)
        now = self._clock()
        opened = self._opened.get(folder)
        if opened is not None and now - opened < RECENT_OPEN_S and not args.rouvrir:
            since = round(now - opened)
            return ToolOutput(
                f"Le terminal est déjà ouvert dans {where} (depuis {since} s) : ne le rouvre "
                "pas. L'utilisateur y écrit ses demandes, et tu y lances les "
                "commandes. S'il dit l'avoir fermé, rouvre-le avec rouvrir=true.",
                {"dossier": str(folder), "deja_ouvert": True},
            )
        self._launch(command)
        self._opened[folder] = now
        return ToolOutput(
            f"Mode shell ouvert dans {where} ({name}) : Jarvis t'y attend.",
            {"dossier": str(folder), "terminal": name},
        )
