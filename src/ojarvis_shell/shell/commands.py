"""Classement d'une ligne de commande : lire (1), agir (2), détruire (3), ou refusée.

La ligne est découpée en morceaux (séparés par `|`, `;`, `&&`, `||`, `&`, un retour à la
ligne, et les sous-commandes `$(…)`) ; chaque morceau est classé, le plus haut niveau
l'emporte. En cas de doute, on monte : une lecture mal reconnue devient une action à
valider, jamais l'inverse.

Refusées : `sudo` et ses cousins (c'est toi qui tapes une commande d'administrateur, Jarvis
ne voit jamais ton mot de passe), et toute commande qui lit un fichier de secrets (`.env`,
`.ssh`…) ou fouille tout ton dossier personnel : sa sortie partirait vers un modèle.
"""

from __future__ import annotations

import re
from pathlib import Path

from ojarvis.shell.programs import SPECIAL
from ojarvis.shell.sensitive import (
    METADATA,
    SEARCHERS,
    _check_recursive_search,
    _check_sensitive,
    _expand,
    _prints_secret,
)
from ojarvis.shell.syntax import (
    _CAPTURED,
    ASSIGNMENT,
    REDIRECTS,
    SUBSTITUTED,
    _assignment,
    _defines_function,
    _redirections,
    _segments,
    _substitutions,
    _tokens,
)
from ojarvis.shell.verdict import _PLACES, Verdict, _highest, _skip_options
from ojarvis.tools.base import Level
from ojarvis.tools.guards import (
    BlockedTargetError,
)

ADMIN = frozenset({"sudo", "sudoedit", "su", "doas", "pkexec", "run0"})
#: Enveloppes qui lancent la commande qui suit, avec leurs options suivies d'une valeur.
WRAPPERS: dict[str, frozenset[str]] = {
    "time": frozenset(),
    "nohup": frozenset(),
    "command": frozenset(),
    "builtin": frozenset(),
    "exec": frozenset(),
    "nice": frozenset({"-n"}),
    "ionice": frozenset({"-c", "-n"}),
    "timeout": frozenset({"-s", "-k", "--signal", "--kill-after"}),
    "stdbuf": frozenset({"-i", "-o", "-e"}),
    "env": frozenset({"-u", "-C", "--unset", "--chdir"}),
    "setsid": frozenset(),
    "flock": frozenset({"-w", "--timeout", "-E", "--conflict-exit-code"}),
    "taskset": frozenset(),
    "chrt": frozenset(),
    "unshare": frozenset({"-S", "-G", "-R", "-w", "--setuid", "--setgid", "--root", "--wd"}),
    "strace": frozenset({"-o", "-e", "-s", "-p", "-u", "-E", "-P", "-a", "-b", "-I", "-O", "-S"}),
    "ltrace": frozenset({"-o", "-e", "-s", "-p", "-u", "-n", "-a", "-A", "-D", "-L", "-x"}),
    "systemd-run": frozenset(
        {
            "-u",
            "--unit",
            "-p",
            "--property",
            "--description",
            "-E",
            "--setenv",
            "--slice",
            "--uid",
            "--gid",
            "-M",
            "--machine",
        }
    ),
}
#: Enveloppes dont le premier mot après les options n'est pas la commande : le fichier de
#: verrou de flock, le masque de taskset, la priorité de chrt.
WRAPPER_VALUE = frozenset({"flock", "taskset", "chrt"})
#: Lancent un texte comme une ligne de commande : « watch 'rm -rf x' », « script -c '…' ».
TEXT_RUNNERS: dict[str, frozenset[str]] = {
    "watch": frozenset({"-n", "--interval", "-d"}),
    "script": frozenset({"-E", "--echo", "-I", "--log-in", "-O", "--log-out", "-T", "-B"}),
}
#: Dossiers des programmes du système : « /usr/bin/ls » est ls ; « ./ls », un programme à soi.
SYSTEM_BIN = frozenset({"/usr/bin", "/bin", "/usr/sbin", "/sbin", "/usr/local/bin"})
SHELLS = frozenset({"bash", "sh", "zsh", "dash", "fish", "ksh"})

# fmt: off
READ_ONLY = frozenset({
    "ls", "ll", "la", "dir", "cat", "tac", "head", "tail", "less", "more", "wc", "nl",
    "grep", "egrep", "fgrep", "rg", "ag", "du", "df", "free", "ps", "pstree", "pgrep",
    "uptime", "w", "who", "whoami", "id", "groups", "uname", "date", "cal", "pwd", "echo",
    "printf", "true", "false", "test", "[", "which", "whereis", "type", "file", "stat",
    "tree", "cut", "paste", "column", "tr", "diff", "cmp", "comm", "md5sum",
    "sha1sum", "sha256sum", "basename", "dirname", "realpath", "readlink", "seq", "sleep",
    "lsblk", "lscpu", "lspci", "lsusb", "lsmod", "lshw", "dmesg", "nproc", "getconf",
    "locale", "printenv", "ss", "netstat", "jq",
    "hexdump", "od", "strings", "zcat", "zgrep", "bzcat", "xzcat", "locate", "top",
    "vmstat", "iostat", "mpstat", "sensors", "acpi", "upower", "lsof", "history", "man",
    "help", "whatis", "apropos", "tldr", "neofetch", "fastfetch", "cd", "export", "alias",
    "unalias", "set", "unset", ":",
})
DESTRUCTIVE = frozenset({
    "rm", "rmdir", "unlink", "shred", "srm", "dd", "wipefs", "fdisk", "sfdisk", "cfdisk",
    "parted", "gdisk", "mkswap", "truncate", "kill", "killall", "pkill", "xkill",
    "shutdown", "reboot", "poweroff", "halt", "init", "telinit", "chown", "chgrp",
    "userdel", "groupdel",
})
# fmt: on
#: Interrogent le réseau : une donnée lue (glissée dans un nom de domaine) pourrait y sortir.
NETWORK_LOOKUPS = frozenset(
    {"ping", "ping6", "dig", "host", "nslookup", "traceroute", "tracepath", "mtr", "whois"}
)
#: Interpréteurs : lus seuls (`--version`), ils exécutent du code sinon. « python3 version »
#: lance le script « version ».
INTERPRETERS = frozenset(
    {"python", "python3", "node", "deno", "bun", "ruby", "perl", "php", "lua", "java"}
)
VERSION_ONLY = frozenset({"--version", "-V", "-version", "--help", "-h"})
#: Lancent un paquet ou un programme nommé : « npx paquet --help » exécute le paquet.
RUNNERS = frozenset({"npx", "bunx", "pnpx", "uvx", "pipx"})


def classify(command: str, *, cwd: Path, home: Path) -> Verdict:
    """Le niveau de la ligne entière : celui de son morceau le plus risqué."""
    text = command.strip()
    if not text:
        raise BlockedTargetError("Commande vide.")
    return _classify(text, cwd=cwd, home=home, depth=0)


# --------------------------------------------------------------------------- découpage


def _classify(text: str, *, cwd: Path, home: Path, depth: int) -> Verdict:
    if depth > 4:
        return Verdict(Level.AGIR, "commandes imbriquées trop profondément")
    verdicts: list[Verdict] = []
    text, inner = _substitutions(text)
    if inner:
        # `$(…)`, `` `…` ``, `<(…)` : exécutés même entre guillemets ; classés à part. Leur
        # sortie reste au shell (un jeton passé à curl), d'où « captée ».
        verdicts.append(Verdict(Level.AGIR, "sous-commande"))
        captured = _CAPTURED.set(True)
        try:
            for part in inner:
                verdicts.append(_classify(part, cwd=cwd, home=home, depth=depth + 1))
        finally:
            _CAPTURED.reset(captured)
    try:
        tokens = _tokens(text)
    except ValueError:
        unreadable = Verdict(Level.AGIR, "ligne que le classement ne sait pas lire")
        return _highest([*verdicts, unreadable])
    if _defines_function(tokens):
        verdicts.append(Verdict(Level.DETRUIRE, "définition de fonction (bombe possible)"))
    places = _PLACES.set(_PLACES.get())
    try:
        for segment, piped in _segments(tokens):
            verdicts.append(_segment(segment, piped=piped, cwd=cwd, home=home, depth=depth))
            moved = _moved(segment, cwd=cwd, home=home)
            if isinstance(moved, Verdict):
                verdicts.append(moved)
            elif moved is not None:
                # « cd ~/.config && cat gh/hosts.yml » : la suite se lit aussi depuis là.
                _PLACES.set((*_PLACES.get(), moved))
    finally:
        _PLACES.reset(places)
    return _highest(verdicts or [Verdict(Level.LIRE, "rien à lancer")])


# --------------------------------------------------------------------------- un morceau


def _segment(tokens: list[str], *, piped: bool, cwd: Path, home: Path, depth: int) -> Verdict:
    words, floor = _redirections(tokens, cwd=cwd, home=home)
    while words and ASSIGNMENT.fullmatch(words[0]):
        _check_sensitive(words[0], cwd=cwd, home=home)
        floor = _highest([floor, _assignment(words[0])])
        words = words[1:]
    if not words:
        return floor
    name = Path(words[0]).name
    if name in ADMIN or (name == "machinectl" and words[1:2] in (["shell"], ["login"])):
        raise BlockedTargetError(
            f"Jarvis ne lance jamais « {name} » : il ne voit pas ton mot de passe. En mode "
            "shell, propose-la avec proposer_sudo : l'utilisateur la lance lui-même, en un "
            "appui ; ailleurs, montre-la pour qu'il la tape lui-même."
        )
    if not _CAPTURED.get() and _prints_secret(name, words[1:]):
        raise BlockedTargetError(
            f"« {' '.join(words[:4])} » affiche un secret : sa sortie partirait vers le modèle. "
            "Utilise-le seulement dans une sous-commande, par exemple "
            '-H "Authorization: Bearer $(gcloud auth print-access-token)".'
        )
    launched = _launcher(name, words, piped=piped, cwd=cwd, home=home, depth=depth)
    if launched is not None:
        return _highest([floor, launched])
    if name not in METADATA:
        for word in words[1:]:
            _check_sensitive(word, cwd=cwd, home=home)
    if name in SEARCHERS:
        too_large = _check_recursive_search(name, words[1:], cwd=cwd, home=home)
        floor = _highest([floor, too_large]) if too_large else floor
    verdict = _command(name, words[1:], piped=piped, cwd=cwd, home=home, depth=depth)
    return _highest([floor, verdict])


def _launcher(  # noqa: PLR0911 - une table de décision, lue de haut en bas
    name: str, words: list[str], *, piped: bool, cwd: Path, home: Path, depth: int
) -> Verdict | None:
    """Ce qui lance une autre commande ou un programme à soi : la commande lancée est classée.
    None : un programme ordinaire, classé par `_command`."""
    if "/" in words[0] and str(Path(words[0]).parent) not in SYSTEM_BIN:
        # « ./deploy.sh --help », « ./ls » : un programme à soi, quel que soit son nom.
        for word in words[1:]:
            _check_sensitive(word, cwd=cwd, home=home)
        return Verdict(Level.AGIR, f"{words[0]} : un programme du dossier")
    if name in TEXT_RUNNERS or (name == "flock" and {"-c", "--command"} & set(words[1:])):
        return _text_runner(name, words[1:], cwd=cwd, home=home, depth=depth)
    if name not in WRAPPERS:
        return None
    rest = _skip_options(words[1:], WRAPPERS[name])
    floor = Verdict(Level.LIRE, f"{name} seul")
    if name == "timeout" and rest and re.fullmatch(r"[0-9.]+[smhd]?", rest[0]):
        rest = rest[1:]
    if name in WRAPPER_VALUE:
        if "-p" in words[1:] or "--pid" in words[1:]:
            return Verdict(Level.AGIR, f"{name} change un processus")
        rest = rest[1:]
    if name == "env":
        split = [w.split("=", 1)[1] for w in words[1:] if w.startswith("--split-string=")]
        if split or {"-S", "--split-string"} & set(words[1:]):
            # « env -S 'rm -rf x' » : le texte devient une commande.
            inner = _classify(" ".join([*split, *rest]), cwd=cwd, home=home, depth=depth + 1)
            return _highest([Verdict(Level.AGIR, "env -S lance un texte"), inner])
        floor = _highest([floor, *(_assignment(w) for w in rest if ASSIGNMENT.fullmatch(w))])
        rest = [w for w in rest if not ASSIGNMENT.fullmatch(w)]
    if not rest:
        return floor
    return _highest([floor, _segment(rest, piped=piped, cwd=cwd, home=home, depth=depth)])


def _text_runner(name: str, args: list[str], *, cwd: Path, home: Path, depth: int) -> Verdict:
    """« watch 'rm -rf x' », « script -c '…' », « flock f -c '…' » : le texte est une ligne
    de commande, classée comme telle."""
    if "-c" in args or "--command" in args:
        index = args.index("-c" if "-c" in args else "--command") + 1
        text = args[index] if index < len(args) else ""
    elif name == "watch":
        text = " ".join(_skip_options(args, TEXT_RUNNERS[name]))
    else:
        return Verdict(Level.AGIR, f"{name} ouvre un shell")
    inner = _classify(text, cwd=cwd, home=home, depth=depth + 1) if text.strip() else None
    return _highest([Verdict(Level.AGIR, f"{name} lance un texte"), *([inner] if inner else [])])


def _moved(segment: list[str], *, cwd: Path, home: Path) -> Path | Verdict | None:
    """Le dossier où mène un `cd` (ou `pushd`) : les morceaux suivants s'y lancent."""
    words = [w for w in segment if w not in REDIRECTS]
    while words and ASSIGNMENT.fullmatch(words[0]):
        words = words[1:]
    if not words or words[0] not in ("cd", "pushd"):
        return None
    if words[1:] == ["-"]:
        return Verdict(Level.AGIR, "cd - mène à un dossier connu seulement à l'exécution")
    targets = [w for w in words[1:] if not w.startswith("-")]
    if not targets:
        return home
    text = _expand(targets[0], home)
    if "$" in text or SUBSTITUTED in text:
        return Verdict(Level.AGIR, "cd mène à un dossier connu seulement à l'exécution")
    base = (_PLACES.get() or (cwd,))[-1]
    return Path(text) if text.startswith("/") else base / text


# --------------------------------------------------------------------------- secrets


# --------------------------------------------------------------------------- par commande


def _command(  # noqa: PLR0911, PLR0912 - une table de décision, lue de haut en bas
    name: str, args: list[str], *, piped: bool, cwd: Path, home: Path, depth: int
) -> Verdict:
    if name in DESTRUCTIVE or name.startswith("mkfs"):
        return Verdict(Level.DETRUIRE, f"{name} supprime, écrase ou arrête")
    if name in SHELLS:
        if "-c" in args:
            index = args.index("-c") + 1
            script = args[index] if index < len(args) else ""
            inner = _classify(script, cwd=cwd, home=home, depth=depth + 1)
            return _highest([Verdict(Level.AGIR, f"{name} -c"), inner])
        if piped:
            return Verdict(Level.DETRUIRE, f"un script reçu par un tube, exécuté par {name}")
        return Verdict(Level.AGIR, f"{name} exécute un script")
    if name in ("eval", "source", "."):
        inner = _classify(" ".join(args), cwd=cwd, home=home, depth=depth + 1)
        return _highest([Verdict(Level.AGIR, f"{name} exécute du texte"), inner])
    if name == "xargs":
        rest = _skip_options(args, frozenset({"-n", "-I", "-P", "-L", "-s", "-d", "-E", "-a"}))
        if not rest:
            return Verdict(Level.LIRE, "xargs echo")
        inner = _segment(rest, piped=False, cwd=cwd, home=home, depth=depth)
        if Path(rest[0]).name in METADATA | {"echo", "printf"}:
            return inner
        # Les noms arrivent par le tube, on ne les voit pas : « xargs cat » lirait n'importe
        # quel fichier, secrets compris. Tu valides.
        return _highest([Verdict(Level.AGIR, f"xargs {rest[0]} sur des noms reçus"), inner])
    if name in INTERPRETERS:
        if args and all(a in VERSION_ONLY for a in args):
            return Verdict(Level.LIRE, f"{name} --version")
        return Verdict(Level.AGIR, f"{name} exécute du code")
    if name == "export":
        declared = [_assignment(a) for a in args if ASSIGNMENT.fullmatch(a)]
        return _highest([Verdict(Level.LIRE, "export"), *declared])
    if _asks_help(name, args):
        return Verdict(Level.LIRE, f"{name} --help")
    special = SPECIAL.get(name)
    if special is not None:
        return special(name, args, cwd, home, depth)
    if name in NETWORK_LOOKUPS:
        return Verdict(Level.AGIR, f"{name} interroge le réseau")
    if name in READ_ONLY:
        return Verdict(Level.LIRE, f"{name} lit")
    if args and all(a == "--version" for a in args):
        return Verdict(Level.LIRE, f"{name} --version")
    return Verdict(Level.AGIR, f"{name} agit")


def _asks_help(name: str, args: list[str]) -> bool:
    """« --help » avant tout ce qui serait lancé : `git commit --help` lit, mais `npx paquet
    --help` ou `docker run image --help` lancent le paquet ou l'image avec l'option."""
    before = args[: args.index("--")] if "--" in args else args
    if "--help" not in before:
        return False
    named = [a for a in before[: before.index("--help")] if not a.startswith("-")]
    return len(named) <= (1 if name in SUBCOMMANDS and name not in RUNNERS else 0)


# fmt: on


# fmt: on


# fmt: on


#: Commandes à « sous-commande » : le type d'une commande est le programme et sa sous-commande
#: (« npm install », « git commit »).
SUBCOMMANDS = frozenset(
    {"git", "npm", "pnpm", "yarn", "npx", "docker", "podman", "cargo", "uv", "pip", "pip3",
     "make", "go", "dotnet", "deno", "bun", "composer", "bundle", "mvn", "gradle", "flutter",
     "dart", "poetry", "pipx", "systemctl", "snap", "flatpak", "apt", "apt-get"}
)  # fmt: skip
