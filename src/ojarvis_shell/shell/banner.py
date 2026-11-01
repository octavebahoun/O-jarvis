"""L'accueil du terminal et son invite : le badge O-jarvis, le dossier et sa branche git, les
modèles qui répondent, le moteur rejoint, et les commandes « / »."""

from __future__ import annotations

import contextlib
import shutil
import subprocess
from pathlib import Path

import httpx

from ojarvis import __version__
from ojarvis.shell.display import BLACK, BOLD, CYAN, DIM, MAGENTA, Style, Terminal
from ojarvis.tools.guards import shown

#: Les modèles du profil « Tout Meta » (`llm/policy.py`), dans ce terminal.
META_MODELS = "Muse Spark 1.3, secours Muse Spark Open, relecture Muse Wonder"

#: Les commandes du terminal, et ce qu'elles font.
COMMANDS = (
    ("/voix", "parler"),
    ("/partage", "écran en direct"),
    ("/nouveau", "conversation"),
    ("/toujours", "validé d'office"),
    ("exit", ""),
)


def branch(folder: Path) -> str | None:
    """La branche git du dossier, s'il est dans un dépôt."""
    git = shutil.which("git")
    if git is None:
        return None
    try:
        found = subprocess.run(  # noqa: S603 - git, sur ton dossier, en lecture
            [git, "-C", str(folder), "symbolic-ref", "--short", "-q", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return found.stdout.strip() or None


def prompt(style: Style, folder: Path, home: Path) -> str:
    """« boutique main ❯ » : le dossier, sa branche git (relue à chaque fois), puis la flèche."""
    name = "~" if folder == home else (folder.name or str(folder))
    text = "\n" + style.prompt(name, BOLD)
    current = branch(folder)
    if current is not None:
        text += " " + style.prompt(current, MAGENTA)
    return text + " " + style.prompt("❯", BOLD, CYAN) + " "


def banner(
    terminal: Terminal,
    http: httpx.Client,
    style: Style,
    folder: Path,
    home: Path,
    *,
    own_engine: bool,
) -> None:
    meta = False
    with contextlib.suppress(httpx.HTTPError, ValueError):
        meta = http.get("/v1/settings").json().get("model_profile") == "meta"
    terminal.line("")
    terminal.line(
        "  "
        + style(" ◉ O-jarvis ", BOLD, BLACK, "46")
        + " "
        + style("Shell", BOLD)
        + style(f"  v{__version__}", DIM)
    )
    terminal.line("")
    current = branch(folder)
    place = shown(folder, home) + (
        style("  git ", DIM) + style(current, MAGENTA) if current else ""
    )
    if meta:
        models = style("Tout Meta", CYAN) + style(" · ", DIM) + META_MODELS
    else:
        models = "profil Automatique (plusieurs fournisseurs)"
    engine = "lancé pour ce terminal, il s'arrête avec lui" if own_engine else "celui de ton app"
    for label, value in (("dossier", place), ("modèles", models), ("moteur", engine)):
        terminal.line("    " + style(f"{label:<9}", DIM) + value)
    terminal.line("")
    commands = "   ".join(
        style(name, CYAN) + (style(" " + what, DIM) if what else "") for name, what in COMMANDS
    )
    terminal.line("    " + commands)
    terminal.line(style("  " + "─" * (terminal.width() - 2), DIM))
