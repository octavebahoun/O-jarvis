"""Garde des secrets du mode shell : une commande qui lirait un fichier de secrets (`.env`,
`~/.ssh`, trousseaux), en afficherait un (jeton, mot de passe) ou fouillerait tout ton dossier
est refusée, car sa sortie partirait vers le modèle.
"""

from __future__ import annotations

import glob
import os
from collections.abc import Iterator
from fnmatch import fnmatch
from pathlib import Path

from ojarvis.shell.verdict import _PLACES, Verdict, _in_order
from ojarvis.tools.base import Level
from ojarvis.tools.guards import (
    SENSITIVE_DIRS,
    SENSITIVE_NAMES,
    SENSITIVE_SUFFIXES,
    BlockedTargetError,
)

#: Commandes qui affichent un secret (jeton, mot de passe) : leur sortie partirait vers le
#: modèle. Permises seulement dans une sous-commande (« -H "Bearer $(…)" »). Ajoutées le
#: 26/10/2026, après qu'un essai a affiché le début d'un jeton Google.
SECRET_PRINTERS = (
    ("gcloud", "auth", "print-access-token"),
    ("gcloud", "auth", "print-identity-token"),
    ("gcloud", "auth", "application-default", "print-access-token"),
    ("gcloud", "secrets", "versions", "access"),
    ("gh", "auth", "token"),
    ("az", "account", "get-access-token"),
    ("aws", "sts", "get-session-token"),
    ("aws", "configure", "get"),
    ("aws", "ecr", "get-login-password"),
    ("aws", "secretsmanager", "get-secret-value"),
    ("vault", "read"),
    ("vault", "kv", "get"),
    ("op", "read"),
    ("secret-tool", "lookup"),
    ("heroku", "auth:token"),
)

#: Ne montrent que des noms, des tailles ou des types, jamais un contenu : un dossier de
#: secrets peut y figurer (« du -sh ~/.* » pour un disque plein).
METADATA = frozenset({"ls", "ll", "la", "dir", "du", "df", "stat", "tree", "find", "file", "wc"})

SEARCHERS = frozenset({"grep", "egrep", "fgrep", "rg", "ag", "zgrep"})

#: Fouille bornée avant une recherche récursive : assez pour trouver le `.env` d'un projet,
#: sans parcourir des milliers de fichiers.
SCAN_DEPTH = 3

SCAN_LIMIT = 5000

SCAN_SKIPPED = frozenset({".git", "node_modules", ".venv", "venv", "__pycache__", ".cache"})

#: Options de rg et ag qui fouillent aussi les fichiers cachés (`.env`).
SEARCH_HIDDEN = frozenset({"--hidden", "-.", "-u", "-uu", "-uuu", "--unrestricted"})


def _expand(word: str, home: Path) -> str:
    text = word.split("=", 1)[1] if "=" in word and not word.startswith(("/", ".")) else word
    text = text.replace("${HOME}", str(home)).replace("$HOME", str(home))
    if text == "~" or text.startswith("~/"):
        text = str(home) + text[1:]
    return text


def _check_sensitive(word: str, *, cwd: Path, home: Path) -> None:
    if word.startswith(("--exclude", "--include", "--glob", "--iglob")):
        return  # des motifs de noms à éviter ou à choisir, pas des fichiers lus
    text = _expand(word, home)  # « --file=~/.env » compris
    if not text or text.startswith("-"):
        return
    bases = [Path("/")] if text.startswith("/") else [cwd, *_PLACES.get()]
    for base in bases:
        path = base / text
        matches = [Path(p) for p in glob.glob(str(path))] if glob.has_magic(str(path)) else []
        if any(_is_sensitive(candidate, home) for candidate in [path, *matches]):
            raise BlockedTargetError(
                f"« {word} » touche un fichier de secrets : son contenu partirait vers le "
                "modèle. Refusé."
            )


def _relative_to_home(path: Path, home: Path) -> str | None:
    try:
        return path.resolve(strict=False).relative_to(home.resolve()).as_posix()
    except ValueError:
        return None


def _is_sensitive(path: Path, home: Path) -> bool:
    relative = _relative_to_home(path, home)
    if relative is not None and any(
        relative == d or relative.startswith(d + "/") for d in SENSITIVE_DIRS
    ):
        return True
    name = path.name
    return (
        name in SENSITIVE_NAMES
        or name.startswith(".env.")
        or name.endswith(SENSITIVE_SUFFIXES)
        or name.startswith(("id_rsa", "id_ed25519", "id_ecdsa", "id_dsa"))  # clé copiée
    )


def _holds_secrets(path: Path, home: Path) -> bool:
    """Un dossier qui contient un dossier de secrets (ton dossier personnel, `~/.config`…)."""
    resolved, root = path.resolve(strict=False), home.resolve()
    if resolved == root or resolved in root.parents:
        return True
    relative = _relative_to_home(resolved, root)
    return relative is not None and any(d.startswith(relative + "/") for d in SENSITIVE_DIRS)


class ScanIncompleteError(Exception):
    """Le dossier est trop grand ou trop profond pour vérifier qu'il n'a aucun secret."""


def _check_recursive_search(name: str, args: list[str], *, cwd: Path, home: Path) -> Verdict | None:
    """Refuse une recherche qui lirait un fichier de secrets ; si le dossier est trop grand
    pour le vérifier en entier, la recherche n'est plus une simple lecture : tu valides."""
    if not _recursive(name, args):
        return None
    bases = [cwd, *_PLACES.get()]
    folders = [
        base / text
        for text in (_expand(a, home) for a in args if not a.startswith("-"))
        for base in bases
    ]
    roots = [f for f in folders if f.is_dir()] or bases
    if any(_holds_secrets(root, home) for root in roots):
        raise BlockedTargetError(
            f"{name} fouillerait aussi tes dossiers de secrets ({', '.join(map(str, roots))}). "
            "Cherche dans un dossier de projet précis."
        )
    # rg et ag sautent les fichiers cachés (un `.env`), sauf si on le leur demande.
    hidden = name in ("rg", "ag") and not SEARCH_HIDDEN & set(args)
    excluded = _excluded(args)
    for root in roots:
        try:
            for found in _secret_files(root, home, skip_hidden=hidden):
                if not any(fnmatch(found.name, pattern) for pattern in excluded):
                    raise BlockedTargetError(
                        f"{name} lirait aussi « {found.name} », un fichier de secrets : son "
                        "contenu partirait vers le modèle. Cherche dans un sous-dossier précis, "
                        f"ou exclus-le (--exclude='{found.name}' pour grep, -g '!{found.name}' "
                        "pour rg)."
                    )
        except ScanIncompleteError:
            return Verdict(
                Level.AGIR,
                f"{name} fouille un dossier trop grand pour vérifier qu'il n'a aucun secret",
            )
    return None


def _recursive(name: str, args: list[str]) -> bool:
    """grep -r, -R, -d recurse… ; rg et ag fouillent toujours les sous-dossiers."""
    if name in ("rg", "ag"):
        return True
    for index, arg in enumerate(args):
        if arg in ("--recursive", "--dereference-recursive", "--directories=recurse"):
            return True
        if arg in ("-d", "--directories") and args[index + 1 : index + 2] == ["recurse"]:
            return True
        if arg.startswith("-") and not arg.startswith("--") and ("r" in arg or "R" in arg):
            return True  # « -rn », « -drecurse » compris
    return False


def _excluded(args: list[str]) -> list[str]:
    """Les fichiers exclus d'une recherche : grep --exclude=…, rg -g '!…'."""
    patterns: list[str] = []
    for index, arg in enumerate(args):
        value = args[index + 1] if index + 1 < len(args) else ""
        if arg.startswith("--exclude="):
            patterns.append(arg.removeprefix("--exclude="))
        elif arg == "--exclude":
            patterns.append(value)
        elif arg.startswith(("--glob=!", "--iglob=!")):
            patterns.append(arg.split("=!", 1)[1])
        elif arg in ("-g", "--glob", "--iglob") and value.startswith("!"):
            patterns.append(value[1:])
    return patterns


def _secret_files(root: Path, home: Path, *, skip_hidden: bool) -> Iterator[Path]:
    """Les fichiers de secrets qu'une recherche récursive lirait sous ce dossier."""
    seen = 0
    for folder, dirs, files in os.walk(root):
        here = Path(folder)
        dirs[:] = [
            d for d in dirs if d not in SCAN_SKIPPED and not (skip_hidden and d.startswith("."))
        ]
        if dirs and len(here.relative_to(root).parts) >= SCAN_DEPTH:
            # Plus profond que ce qu'on vérifie : un secret pourrait s'y cacher.
            raise ScanIncompleteError
        for name in files:
            seen += 1
            if seen > SCAN_LIMIT:
                raise ScanIncompleteError
            if not (skip_hidden and name.startswith(".")) and _is_sensitive(here / name, home):
                yield here / name


def _prints_secret(name: str, args: list[str]) -> bool:
    words = [a for a in args if not a.startswith("-")]
    if name == "kubectl" and words[:2] == ["config", "view"]:
        return "--raw" in args
    if name == "pass":  # `pass` seul liste des noms ; `pass x` affiche un mot de passe
        return bool(words) and words[0] not in ("ls", "list", "find", "search")
    return any(name == p[0] and _in_order(p[1:], words) for p in SECRET_PRINTERS)
