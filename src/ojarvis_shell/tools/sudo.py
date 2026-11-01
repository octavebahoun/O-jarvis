"""sudo en un appui : Jarvis ne lance jamais une commande d'administrateur ; il la propose,
et c'est toi qui la lances dans ton terminal, où `sudo` te demande ton mot de passe.
"""

from __future__ import annotations

import re
from fnmatch import fnmatch
from pathlib import Path
from typing import ClassVar

from pydantic import Field

from ojarvis.shell.commands import classify
from ojarvis.tools.base import Level, Tool, ToolArgs, ToolContext, ToolOutput
from ojarvis.tools.guards import BlockedTargetError

#: Fichiers système de secrets : leur contenu partirait vers le modèle avec la sortie.
SYSTEM_SECRETS = ("/etc/shadow", "/etc/gshadow", "/etc/sudoers", "/root", "/etc/ssh/ssh_host_")

#: « sudo », ses options et leur valeur (« -u postgres »), au début de chaque morceau.
SUDO_PREFIX = re.compile(r"(^|[;&|(]\s*)sudo(?:\s+(?:-[ughpUCDrtT]\s+\S+|-\S+))*\s+")

#: Des chemins sous les secrets à préfixe : un motif (« /etc/ssh/* ») qui les couvre est refusé.
SECRET_SAMPLES = ("/etc/shadow", "/etc/gshadow", "/etc/sudoers", "/root/x", "/etc/ssh/ssh_host_x")


def _reads_system_secret(command: str) -> bool:
    """« /etc//shadow », « /etc/shad* », « /etc/ssh/* » désignent aussi un fichier de secrets."""
    for word in re.split(r"[\s;&|()<>=\"']+", command):
        path = re.sub(r"/+", "/", word.replace("/./", "/"))
        if not path.startswith("/"):
            continue
        if any(secret in path for secret in SYSTEM_SECRETS):
            return True
        if set(path) & set("*?[") and any(fnmatch(s, path) for s in SECRET_SAMPLES):
            return True
    return False


def admin_level(command: str, *, home: Path) -> Level:
    """Le niveau de ce que fait la commande sous sudo. Refusée si elle ne commence pas par
    sudo, si sudo s'y cache, ou si elle lit un fichier de secrets : sa sortie te reviendra."""
    if not command.startswith("sudo "):
        raise BlockedTargetError(
            "Une commande proposée ici commence par sudo ; les autres se lancent avec "
            "executer_commande."
        )
    if _reads_system_secret(command):
        raise BlockedTargetError(
            "Cette commande lit un fichier de secrets du système : sa sortie partirait vers le "
            "modèle. Refusée."
        )
    inner = SUDO_PREFIX.sub(r"\1", command).strip()
    if not inner:
        raise BlockedTargetError("sudo seul : rien à lancer.")
    return classify(inner, cwd=home, home=home).level


class AdminArgs(ToolArgs):
    commande: str = Field(
        min_length=6,
        max_length=2000,
        description="La commande complète, sudo devant (« sudo apt install -y ncdu »).",
    )
    pourquoi: str = Field(
        min_length=3,
        max_length=300,
        description="Pourquoi elle demande les droits d'administrateur, en une phrase.",
    )


class ProposeAdmin(Tool[AdminArgs]):
    """Une commande d'administrateur : le terminal la montre, l'utilisateur la lance lui-même.

    Jarvis ne lance jamais sudo. Ici, il propose la commande ; le terminal demande
    « [e]xécuter moi-même ? » ; elle tourne dans le terminal de l'utilisateur, où sudo lui
    demande son mot de passe directement (Jarvis ne le voit jamais) ; sa sortie revient
    ensuite au modèle, masquée, comme une donnée."""

    name: ClassVar[str] = "proposer_sudo"
    description: ClassVar[str] = (
        "Propose une commande qui demande sudo : tu ne la lances jamais toi-même. Le terminal "
        "la montre à l'utilisateur, qui la lance lui-même en un appui ; sudo lui demande son "
        "mot de passe, et la sortie te revient pour que tu continues."
    )
    Args: ClassVar[type[ToolArgs]] = AdminArgs
    level: ClassVar[Level] = Level.LIRE
    shell_only: ClassVar[bool] = True

    def describe(self, args: AdminArgs) -> str:
        return f"Proposer « {args.commande} », que tu lances toi-même"

    async def run(self, args: AdminArgs, ctx: ToolContext) -> ToolOutput:
        command = args.commande.strip()
        level = admin_level(command, home=ctx.home)
        return ToolOutput(
            "Proposée : l'utilisateur la lance lui-même s'il le veut, et sa sortie te reviendra "
            "avec son prochain message. Attends-la.",
            {"sudo": {"command": command, "level": int(level), "why": args.pourquoi}},
        )
