"""Signatures des commandes (« git status », « npm test ») : ce que « toujours pour cette
session » valide d'office, et les commandes qui parlent au réseau.
"""

from __future__ import annotations

import re
from pathlib import Path

from ojarvis.shell.commands import INTERPRETERS, NETWORK_LOOKUPS, SUBCOMMANDS, WRAPPERS, _segment
from ojarvis.shell.programs import GIT_OPTIONS
from ojarvis.shell.syntax import (
    ASSIGNMENT,
    _defines_function,
    _redirections,
    _segments,
    _substitutions,
    _tokens,
)
from ojarvis.shell.verdict import _skip_options
from ojarvis.tools.base import Level
from ojarvis.tools.guards import BlockedTargetError

#: Jamais « toujours » : elles parlent au réseau, publient ou agissent dans le cloud. Un
#: contenu piégé pourrait s'en servir pour faire sortir des données : tu valides chaque fois.
NEVER_ALWAYS = frozenset(
    {"curl", "wget", "ssh", "scp", "sftp", "rsync", "nc", "ncat", "netcat", "telnet", "ftp",
     "socat", "gcloud", "aws", "az", "kubectl", "doctl", "heroku", "vercel", "firebase", "gh",
     "git push", "git remote", "git clone", "git fetch", "git pull", "git ls-remote",
     "git submodule", "npm publish", "pnpm publish", "yarn publish", "docker push",
     "docker login", "podman push", "twine", "bash", "sh", "zsh", "eval", "source", ".",
     "xargs", *NETWORK_LOOKUPS,
     # Installer un paquet lance son code (scripts d'installation) : « toujours » pour un
     # paquet ne doit pas valider le suivant, peut-être piégé.
     "npm install", "npm i", "npm add", "npm ci", "npx", "pnpm install", "pnpm i", "pnpm add",
     "pnpm dlx", "pnpx", "yarn add", "yarn install", "yarn dlx", "bunx", "bun add", "bun install",
     "pip install", "pip3 install", "pipx", "uv add", "uv pip", "uvx", "cargo install",
     "cargo add", "gem install", "go install", "go get", "composer require", "composer install"}
)  # fmt: skip

#: Options suivies d'une valeur avant la sous-commande : « git -C ~/projet push » est un push.
SUBCOMMAND_OPTIONS: dict[str, frozenset[str]] = {
    "git": GIT_OPTIONS,
    "npm": frozenset({"--prefix", "-w", "--workspace"}),
    "pnpm": frozenset({"-C", "--dir", "-F", "--filter"}),
    "yarn": frozenset({"--cwd"}),
    "docker": frozenset({"-H", "--host", "--context", "-c", "--config", "-l", "--log-level"}),
    "podman": frozenset({"-c", "--connection", "--url", "--root", "--runroot"}),
    "cargo": frozenset({"-C", "--manifest-path", "-Z", "--config", "--color"}),
    "uv": frozenset({"--directory", "--project", "-p", "--python", "--config-file"}),
    "make": frozenset({"-C", "-f", "--directory", "--file", "--makefile", "-I", "-o", "-W"}),
    "go": frozenset({"-C"}),
}

#: Une sous-commande se reconnaît (« install », « run-script ») : pas un chemin, pas une URL.
SUBCOMMAND_WORD = re.compile(r"[a-z][a-z0-9:_-]*")


def signatures(  # noqa: PLR0911 - une table de décision, lue de haut en bas
    command: str, *, cwd: Path, home: Path
) -> frozenset[str] | None:
    """Les types d'action d'une commande (« npm install »…), pour « toujours pour cette
    session » ; None si elle ne peut pas en bénéficier (destruction, réseau, code à la volée,
    sous-commande, ligne illisible)."""
    text = command.strip()
    rest, inner = _substitutions(text)
    if not text or inner:
        return None
    try:
        tokens = _tokens(rest)
        if _defines_function(tokens):
            return None
        found: set[str] = set()
        for segment, piped in _segments(tokens):
            verdict = _segment(segment, piped=piped, cwd=cwd, home=home, depth=0)
            if verdict.level == Level.LIRE:
                continue  # une lecture n'a pas besoin de « toujours »
            if verdict.level >= Level.DETRUIRE:
                return None
            signature = _signature(segment)
            if signature is None or signature in NEVER_ALWAYS:
                return None
            if signature.split()[0] in NEVER_ALWAYS:
                return None
            found.add(signature)
    except (ValueError, BlockedTargetError):
        return None
    return frozenset(found)


#: Ramènent un contenu d'ailleurs : leur sortie peut porter des consignes piégées.
NETWORK_TOOLS = frozenset(
    {"curl", "wget", "http", "https", "xh", "aria2c", "lynx", "w3m", "ssh", "scp", "sftp",
     "rsync", "nc", "ncat", "netcat", "telnet", "ftp", "socat", "gcloud", "aws", "az", "kubectl",
     "doctl", "heroku", "vercel", "firebase", "gh", *NETWORK_LOOKUPS}
)  # fmt: skip

GIT_NETWORK = frozenset({"clone", "fetch", "pull", "push", "ls-remote", "remote", "submodule"})


def talks_to_network(command: str) -> bool:
    """La commande ramène-t-elle un contenu d'ailleurs (curl, git clone, gh…) ? Sa sortie
    compte alors comme lue sur le web : plus de « toujours » ensuite. Illisible : oui."""
    text, inner = _substitutions(command)
    if any(talks_to_network(part) for part in inner):
        return True
    try:
        segments = _segments(_tokens(text))
        signatures = [_signature(segment) for segment, _piped in segments]
    except (ValueError, BlockedTargetError):
        return True
    for signature in signatures:
        name, _, sub = (signature or "").partition(" ")
        if name in NETWORK_TOOLS or (name == "git" and sub in GIT_NETWORK):
            return True
    return False


def _signature(tokens: list[str]) -> str | None:  # noqa: PLR0911 - une table de décision
    words, _floor = _redirections(tokens, cwd=Path("/"), home=Path("/"))
    while words:
        if ASSIGNMENT.fullmatch(words[0]):  # « env NODE_ENV=production curl » est un curl
            words = words[1:]
            continue
        name = Path(words[0]).name
        if name not in WRAPPERS:
            break
        words = _skip_options(words[1:], WRAPPERS[name])
        if name == "timeout" and words and re.fullmatch(r"[0-9.]+[smhd]?", words[0]):
            words = words[1:]
    if not words:
        return None
    name, args = Path(words[0]).name, words[1:]
    if name in INTERPRETERS:
        if {"-c", "-e", "--eval"} & set(args):
            return None  # du code écrit à la volée : chaque fois
        if "-m" in args:
            index = args.index("-m") + 1
            return f"{name} -m {args[index]}" if index < len(args) else None
        script = next((a for a in args if not a.startswith("-")), None)
        return f"{name} {script}" if script else None
    if name in SUBCOMMANDS:
        rest = _skip_options(args, SUBCOMMAND_OPTIONS.get(name, frozenset()))
        if not rest:
            # « make -j4 » : le programme seul. Une valeur d'option inconnue : jamais.
            return name if all(a.startswith("-") for a in args) else None
        return f"{name} {rest[0]}" if SUBCOMMAND_WORD.fullmatch(rest[0]) else None
    return name
