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

import glob
import re
import shlex
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ojarvis.tools.base import Level
from ojarvis.tools.guards import (
    SENSITIVE_DIRS,
    SENSITIVE_NAMES,
    SENSITIVE_SUFFIXES,
    BlockedTargetError,
)


@dataclass(frozen=True, slots=True)
class Verdict:
    level: Level
    #: Pourquoi ce niveau (le morceau le plus risqué), montré avec le plan à valider.
    reason: str


Special = Callable[[str, list[str], Path, Path, int], Verdict]

PUNCTUATION = ";&|()<>{}"
#: Opérateurs, du plus long au plus court : « };| » se lit « } ; | ».
OPERATORS = (
    "&>>", "<<<", "&&", "||", "|&", ";;", ">>", "&>", ">&", "<&", "<<", ">|", "<>",
    "|", "&", ";", "(", ")", "{", "}", "<", ">",
)  # fmt: skip
SEPARATORS = frozenset({"|", "||", "|&", "&", "&&", ";", ";;", "(", ")", "{", "}"})
REDIRECTS = frozenset({">", ">>", ">|", "&>", "&>>", ">&", "<", "<>", "<&", "<<", "<<<"})
WRITE_REDIRECTS = frozenset({">", ">>", ">|", "&>", "&>>", "<>"})
HARMLESS_DEVICES = frozenset(
    {"/dev/null", "/dev/stdout", "/dev/stderr", "/dev/zero", "/dev/random", "/dev/urandom"}
)
ADMIN = frozenset({"sudo", "su", "doas", "pkexec", "run0"})
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
    "watch": frozenset({"-n", "-d", "--interval"}),
}
SHELLS = frozenset({"bash", "sh", "zsh", "dash", "fish", "ksh"})
#: Ne montrent que des noms, des tailles ou des types, jamais un contenu : un dossier de
#: secrets peut y figurer (« du -sh ~/.* » pour un disque plein).
METADATA = frozenset({"ls", "ll", "la", "dir", "du", "df", "stat", "tree", "find", "file", "wc"})
SEARCHERS = frozenset({"grep", "egrep", "fgrep", "rg", "ag", "zgrep"})

# fmt: off
READ_ONLY = frozenset({
    "ls", "ll", "la", "dir", "cat", "tac", "head", "tail", "less", "more", "wc", "nl",
    "grep", "egrep", "fgrep", "rg", "ag", "du", "df", "free", "ps", "pstree", "pgrep",
    "uptime", "w", "who", "whoami", "id", "groups", "uname", "date", "cal", "pwd", "echo",
    "printf", "true", "false", "test", "[", "which", "whereis", "type", "file", "stat",
    "tree", "sort", "uniq", "cut", "paste", "column", "tr", "diff", "cmp", "comm", "md5sum",
    "sha1sum", "sha256sum", "basename", "dirname", "realpath", "readlink", "seq", "sleep",
    "lsblk", "lscpu", "lspci", "lsusb", "lsmod", "lshw", "dmesg", "nproc", "getconf",
    "locale", "printenv", "ss", "netstat", "ping", "dig", "host", "nslookup", "jq", "xxd",
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
#: Interpréteurs : lus seuls (`--version`), ils exécutent du code sinon.
INTERPRETERS = frozenset(
    {"python", "python3", "node", "deno", "bun", "ruby", "perl", "php", "lua", "java"}
)
VERSION_ONLY = frozenset({"--version", "-V", "--help", "-h", "version"})


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
    inner = _substitutions(text)
    if inner:
        # `$(…)`, `` `…` ``, `<(…)` : exécutés même entre guillemets ; classés à part.
        verdicts.append(Verdict(Level.AGIR, "sous-commande"))
        verdicts.extend(_classify(part, cwd=cwd, home=home, depth=depth + 1) for part in inner)
    try:
        tokens = _tokens(text)
    except ValueError:
        unreadable = Verdict(Level.AGIR, "ligne que le classement ne sait pas lire")
        return _highest([*verdicts, unreadable])
    if _defines_function(tokens):
        verdicts.append(Verdict(Level.DETRUIRE, "définition de fonction (bombe possible)"))
    for segment, piped in _segments(tokens):
        verdicts.append(_segment(segment, piped=piped, cwd=cwd, home=home, depth=depth))
    return _highest(verdicts or [Verdict(Level.LIRE, "rien à lancer")])


def _without_heredocs(text: str) -> str:
    """Retire le texte des « heredocs » (`<<EOF` … `EOF`) : c'est une entrée, pas une commande."""
    lines: list[str] = []
    ending: str | None = None
    strip_tabs = False
    for line in text.splitlines():
        if ending is not None:
            if (line.lstrip("\t") if strip_tabs else line) == ending:
                ending = None
            continue
        lines.append(line)
        match = re.search(r"<<(-?)\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\2", line)
        if match and "<<<" not in line:
            strip_tabs, ending = match.group(1) == "-", match.group(3)
    return "\n".join(lines)


def _tokens(text: str) -> list[str]:
    flat = _without_heredocs(text).replace("\\\n", " ")
    # Un retour à la ligne sépare deux commandes : sans ça, « ls\nrm -rf x » serait « ls ».
    flat = re.sub(r"[\r\n]+", " ; ", flat)
    lexer = shlex.shlex(flat, posix=True, punctuation_chars=PUNCTUATION)
    lexer.whitespace_split = True
    lexer.commenters = ""
    tokens: list[str] = []
    for token in lexer:
        if token and all(char in PUNCTUATION for char in token):
            tokens.extend(_split_operators(token))
        else:
            tokens.append(token)
    return tokens


def _split_operators(run: str) -> list[str]:
    parts: list[str] = []
    while run:
        operator = next((op for op in OPERATORS if run.startswith(op)), run[0])
        parts.append(operator)
        run = run[len(operator) :]
    return parts


def _substitutions(text: str) -> list[str]:
    """Le texte des sous-commandes `$(…)`, `<(…)`, `>(…)` et `` `…` `` (guillemets compris)."""
    found: list[str] = []
    for match in re.finditer(r"[$<>]\(", text):
        start = match.end()
        depth, index = 1, start
        while index < len(text) and depth:
            depth += {"(": 1, ")": -1}.get(text[index], 0)
            index += 1
        found.append(text[start : index - 1] if depth == 0 else text[start:])
    found.extend(re.findall(r"`([^`]*)`?", text))
    return [part for part in found if part.strip()]


def _defines_function(tokens: list[str]) -> bool:
    return any(tokens[i : i + 3] == ["(", ")", "{"] for i in range(len(tokens) - 2))


def _segments(tokens: list[str]) -> list[tuple[list[str], bool]]:
    """Les morceaux, chacun avec « reçoit-il la sortie d'un autre par un tube ? »."""
    segments: list[tuple[list[str], bool]] = []
    current: list[str] = []
    piped = False
    for token in tokens:
        if token in SEPARATORS:
            if current:
                segments.append((current, piped))
            current = []
            piped = token in ("|", "|&")
            continue
        current.append(token)
    if current:
        segments.append((current, piped))
    return segments


def _highest(verdicts: list[Verdict]) -> Verdict:
    return max(verdicts, key=lambda v: v.level)


# --------------------------------------------------------------------------- un morceau


def _segment(tokens: list[str], *, piped: bool, cwd: Path, home: Path, depth: int) -> Verdict:
    words, floor = _redirections(tokens, cwd=cwd, home=home)
    while words and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", words[0]):
        _check_sensitive(words[0], cwd=cwd, home=home)
        words = words[1:]  # « LANG=C ls » : la variable ne change pas le niveau
    if not words:
        return floor
    name = Path(words[0]).name
    if name in ADMIN:
        raise BlockedTargetError(
            f"Jarvis ne lance jamais « {name} » : il ne voit pas ton mot de passe. Montre la "
            "commande à l'utilisateur pour qu'il la tape lui-même."
        )
    if name in WRAPPERS:
        rest = _skip_options(words[1:], WRAPPERS[name])
        if name == "timeout" and rest and re.fullmatch(r"[0-9.]+[smhd]?", rest[0]):
            rest = rest[1:]
        if name == "env":
            rest = [w for w in rest if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", w)]
        if not rest:
            return _highest([floor, Verdict(Level.LIRE, f"{name} seul")])
        inner = _segment(rest, piped=piped, cwd=cwd, home=home, depth=depth)
        return _highest([floor, inner])
    if name not in METADATA:
        for word in words[1:]:
            _check_sensitive(word, cwd=cwd, home=home)
    if name in SEARCHERS:
        _check_recursive_search(name, words[1:], cwd=cwd, home=home)
    verdict = _command(name, words[1:], piped=piped, cwd=cwd, home=home, depth=depth)
    return _highest([floor, verdict])


def _redirections(tokens: list[str], *, cwd: Path, home: Path) -> tuple[list[str], Verdict]:
    """Retire les redirections ; écrire dans un fichier (`>`) est une action."""
    words: list[str] = []
    floor = Verdict(Level.LIRE, "lecture")
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token not in REDIRECTS:
            words.append(token)
            index += 1
            continue
        target = tokens[index + 1] if index + 1 < len(tokens) else ""
        index += 2
        if token in ("<<", "<<<") or target.isdigit() or target == "-":
            continue  # texte fourni en entrée, ou « 2>&1 »
        _check_sensitive(target, cwd=cwd, home=home)
        if token not in WRITE_REDIRECTS or target in HARMLESS_DEVICES:
            continue
        if target.startswith("/dev/") and not target.startswith("/dev/tty"):
            floor = _highest([floor, Verdict(Level.DETRUIRE, f"écrit sur {target}")])
        else:
            floor = _highest([floor, Verdict(Level.AGIR, f"écrit dans {target}")])
    # « 2>/dev/null » : le « 2 » collé à la redirection n'est pas un argument.
    return [w for w in words if not (w.isdigit() and len(w) == 1)], floor


def _skip_options(words: list[str], with_value: frozenset[str]) -> list[str]:
    index = 0
    while index < len(words) and words[index].startswith("-"):
        index += 2 if words[index] in with_value else 1
    return words[index:]


# --------------------------------------------------------------------------- secrets


def _expand(word: str, home: Path) -> str:
    text = word.split("=", 1)[1] if "=" in word and not word.startswith(("/", ".")) else word
    text = text.replace("${HOME}", str(home)).replace("$HOME", str(home))
    if text == "~" or text.startswith("~/"):
        text = str(home) + text[1:]
    return text


def _check_sensitive(word: str, *, cwd: Path, home: Path) -> None:
    text = _expand(word, home)  # « --file=~/.env » compris
    if not text or text.startswith("-"):
        return
    path = Path(text) if text.startswith("/") else cwd / text
    matches = [Path(p) for p in glob.glob(str(path))] if glob.has_magic(str(path)) else []
    if any(_is_sensitive(candidate, home) for candidate in [path, *matches]):
        raise BlockedTargetError(
            f"« {word} » touche un fichier de secrets : son contenu partirait vers le modèle. "
            "Refusé."
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


def _check_recursive_search(name: str, args: list[str], *, cwd: Path, home: Path) -> None:
    recursive = name in ("rg", "ag") or any(
        a in ("--recursive", "--dereference-recursive")
        or (a.startswith("-") and not a.startswith("--") and ("r" in a or "R" in a))
        for a in args
    )
    if not recursive:
        return
    folders = [
        Path(text) if text.startswith("/") else cwd / text
        for text in (_expand(a, home) for a in args if not a.startswith("-"))
    ]
    roots = [f for f in folders if f.is_dir()] or [cwd]
    if any(_holds_secrets(root, home) for root in roots):
        raise BlockedTargetError(
            f"{name} fouillerait aussi tes dossiers de secrets ({', '.join(map(str, roots))}). "
            "Cherche dans un dossier de projet précis."
        )


# --------------------------------------------------------------------------- par commande


def _command(  # noqa: PLR0911 - une table de décision, lue de haut en bas
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
        return _segment(rest, piped=False, cwd=cwd, home=home, depth=depth)
    special = SPECIAL.get(name)
    if special is not None:
        return special(name, args, cwd, home, depth)
    if name in READ_ONLY:
        return Verdict(Level.LIRE, f"{name} lit")
    if name in INTERPRETERS:
        if args and all(a in VERSION_ONLY for a in args):
            return Verdict(Level.LIRE, f"{name} --version")
        return Verdict(Level.AGIR, f"{name} exécute du code")
    if args and all(a in ("--version", "--help") for a in args):
        return Verdict(Level.LIRE, f"{name} --version")
    return Verdict(Level.AGIR, f"{name} agit")


def _sub(args: list[str], with_value: frozenset[str] = frozenset()) -> tuple[str, list[str]]:
    """La sous-commande (« git status » → « status ») et ce qui la suit."""
    rest = _skip_options(args, with_value)
    return (rest[0], rest[1:]) if rest else ("", [])


# fmt: off
GIT_READ = frozenset({
    "", "status", "log", "diff", "show", "blame", "shortlog", "describe", "rev-parse",
    "rev-list", "ls-files", "ls-tree", "ls-remote", "grep", "reflog", "help", "version",
    "whatchanged", "cherry", "count-objects",
})
GIT_BRANCH_LIST = frozenset({"-a", "-r", "-v", "-vv", "--list", "--show-current", "--all"})
# fmt: on


def _git(  # noqa: PLR0911, PLR0912 - une table de décision, lue de haut en bas
    name: str, args: list[str], cwd: Path, home: Path, depth: int
) -> Verdict:
    del name, cwd, home, depth
    sub, rest = _sub(args, frozenset({"-C", "-c", "--git-dir", "--work-tree"}))
    flags = set(rest)
    if sub in GIT_READ:
        return Verdict(Level.LIRE, f"git {sub or '--version'} lit")
    if sub == "clean":
        if flags & {"-n", "--dry-run"}:
            return Verdict(Level.LIRE, "git clean --dry-run")
        return Verdict(Level.DETRUIRE, "git clean supprime des fichiers non suivis")
    if sub == "reset" and "--hard" in flags:
        return Verdict(Level.DETRUIRE, "git reset --hard efface des changements")
    if sub == "push" and (
        flags & {"-f", "--force", "--force-with-lease", "--mirror", "--delete", "-d"}
        or any(a.startswith(("+", ":")) for a in rest)
    ):
        return Verdict(Level.DETRUIRE, "git push forcé ou suppression distante")
    if sub in ("checkout", "restore") and (
        "--" in rest or "." in rest or (sub == "restore" and "--staged" not in flags)
    ):
        return Verdict(Level.DETRUIRE, f"git {sub} efface des changements locaux")
    if sub == "branch":
        if "-D" in flags:
            return Verdict(Level.DETRUIRE, "git branch -D supprime une branche")
        if not rest or flags <= GIT_BRANCH_LIST:
            return Verdict(Level.LIRE, "git branch liste")
    if sub == "stash" and rest[:1] in (["list"], ["show"]):
        return Verdict(Level.LIRE, "git stash list")
    if sub == "stash" and rest[:1] in (["drop"], ["clear"]):
        return Verdict(Level.DETRUIRE, f"git stash {rest[0]} efface")
    if sub in ("filter-branch", "filter-repo") or (sub == "update-ref" and "-d" in flags):
        return Verdict(Level.DETRUIRE, f"git {sub} réécrit l'historique")
    if sub == "remote" and (not rest or rest == ["-v"] or rest[0] in ("show", "get-url")):
        return Verdict(Level.LIRE, "git remote liste")
    if sub == "tag" and (not rest or rest[0] in ("-l", "--list")):
        return Verdict(Level.LIRE, "git tag liste")
    if sub == "config" and flags & {"--get", "--list", "-l", "--get-all", "--show-origin"}:
        return Verdict(Level.LIRE, "git config lit")
    if sub == "worktree" and rest[:1] == ["list"]:
        return Verdict(Level.LIRE, "git worktree list")
    return Verdict(Level.AGIR, f"git {sub} modifie le dépôt")


# fmt: off
DOCKER_DESTROY = frozenset({
    "volume rm", "image rm", "container rm", "network rm", "volume remove", "image remove",
    "container remove",
})
DOCKER_READ = frozenset({
    "ps", "images", "info", "version", "inspect", "logs", "stats", "top", "port", "diff",
    "history", "events", "search", "--version", "system df", "system info", "image ls",
    "image list", "image inspect", "image history", "container ls", "container list",
    "container inspect", "volume ls", "volume list", "volume inspect", "network ls",
    "network list", "network inspect", "context ls", "compose ps", "compose ls",
    "compose config", "compose logs", "compose version",
})
# fmt: on


def _docker(name: str, args: list[str], cwd: Path, home: Path, depth: int) -> Verdict:
    del cwd, home, depth
    sub, rest = _sub(args, frozenset({"-H", "--host", "--context", "-c", "--config"}))
    second = next((a for a in rest if not a.startswith("-")), "")
    pair = f"{sub} {second}".strip()
    if sub in ("rm", "rmi", "kill") or "prune" in (sub, second) or pair in DOCKER_DESTROY:
        return Verdict(Level.DETRUIRE, f"{name} {pair} supprime")
    if pair == "compose down" and set(rest) & {"-v", "--volumes", "--rmi"}:
        return Verdict(Level.DETRUIRE, f"{name} compose down supprime des volumes")
    if sub in DOCKER_READ or pair in DOCKER_READ:
        return Verdict(Level.LIRE, f"{name} {pair} lit")
    return Verdict(Level.AGIR, f"{name} {pair} agit")


# fmt: off
SYSTEMCTL_READ = frozenset({
    "", "status", "show", "cat", "list-units", "list-unit-files", "list-timers",
    "list-sockets", "list-dependencies", "list-jobs", "is-active", "is-enabled", "is-failed",
    "is-system-running", "get-default", "help", "--version",
})
SYSTEMCTL_DESTROY = frozenset({
    "stop", "disable", "mask", "kill", "isolate", "poweroff", "reboot", "halt", "suspend",
    "hibernate", "hybrid-sleep", "rescue", "emergency", "default", "kexec", "soft-reboot",
})
# fmt: on


def _systemctl(name: str, args: list[str], cwd: Path, home: Path, depth: int) -> Verdict:
    del name, cwd, home, depth
    sub, _rest = _sub(args, frozenset({"-t", "--type", "-p", "--property", "-H", "--host"}))
    if sub in SYSTEMCTL_READ:
        return Verdict(Level.LIRE, f"systemctl {sub or 'list-units'} lit")
    if sub in SYSTEMCTL_DESTROY:
        return Verdict(Level.DETRUIRE, f"systemctl {sub} arrête ou désactive")
    return Verdict(Level.AGIR, f"systemctl {sub} agit")


def _journalctl(name: str, args: list[str], cwd: Path, home: Path, depth: int) -> Verdict:
    del name, cwd, home, depth
    if any(a.startswith(("--vacuum", "--rotate", "--flush", "--relinquish-var")) for a in args):
        return Verdict(Level.DETRUIRE, "journalctl efface ou déplace des journaux")
    return Verdict(Level.LIRE, "journalctl lit")


def _find(name: str, args: list[str], cwd: Path, home: Path, depth: int) -> Verdict:
    del name
    if "-delete" in args:
        return Verdict(Level.DETRUIRE, "find -delete supprime")
    for action in ("-exec", "-execdir", "-ok", "-okdir"):
        if action in args:
            start = args.index(action) + 1
            end = next((i for i in range(start, len(args)) if args[i] in (";", "+")), len(args))
            inner = _segment(args[start:end], piped=False, cwd=cwd, home=home, depth=depth)
            return _highest([Verdict(Level.AGIR, f"find {action}"), inner])
    if set(args) & {"-fprint", "-fprint0", "-fprintf", "-fls"}:
        return Verdict(Level.AGIR, "find écrit dans un fichier")
    return Verdict(Level.LIRE, "find cherche")


def _crontab(name: str, args: list[str], cwd: Path, home: Path, depth: int) -> Verdict:
    del name, cwd, home, depth
    if "-r" in args:
        return Verdict(Level.DETRUIRE, "crontab -r efface toutes tes tâches planifiées")
    if "-l" in args:
        return Verdict(Level.LIRE, "crontab -l lit")
    return Verdict(Level.AGIR, "crontab modifie tes tâches planifiées")


def _ip(name: str, args: list[str], cwd: Path, home: Path, depth: int) -> Verdict:
    del cwd, home, depth
    if set(args) & {"add", "del", "delete", "set", "flush", "change", "replace", "append"}:
        return Verdict(Level.AGIR, f"{name} change le réseau")
    return Verdict(Level.LIRE, f"{name} lit")


def _tar(name: str, args: list[str], cwd: Path, home: Path, depth: int) -> Verdict:
    del cwd, home, depth
    first = args[0] if args else ""
    if first in ("-t", "--list") or (first and not first.startswith("--") and "t" in first):
        return Verdict(Level.LIRE, f"{name} liste une archive")
    return Verdict(Level.AGIR, f"{name} crée ou extrait")


def _by_subcommand(read: frozenset[str], destroy: frozenset[str] = frozenset()) -> Special:
    def check(name: str, args: list[str], cwd: Path, home: Path, depth: int) -> Verdict:
        del cwd, home, depth
        sub, _rest = _sub(args)
        if sub in read:
            return Verdict(Level.LIRE, f"{name} {sub} lit".replace("  ", " "))
        if sub in destroy:
            return Verdict(Level.DETRUIRE, f"{name} {sub} supprime")
        return Verdict(Level.AGIR, f"{name} {sub} agit".replace("  ", " "))

    return check


def _with_flag(flags: frozenset[str], high: Level, low: Level) -> Special:
    """Niveau haut si l'une des options est là, collée ou non (« -Rv » contient « -R »)."""
    letters = {f[1] for f in flags if len(f) == 2}

    def check(name: str, args: list[str], cwd: Path, home: Path, depth: int) -> Verdict:
        del cwd, home, depth
        for arg in args:
            short = arg.startswith("-") and not arg.startswith("--")
            if arg in flags or (short and letters & set(arg[1:])):
                return Verdict(high, f"{name} {arg}")
        return Verdict(low, name)

    return check


# fmt: off
PACKAGE_READ = frozenset({
    "list", "ls", "show", "info", "search", "find", "outdated", "view", "why", "freeze",
    "check", "policy", "depends", "rdepends", "changelog", "connections", "services",
    "changes", "history", "remotes", "ps", "tree", "metadata", "--version", "-v", "version",
})
# fmt: on
PACKAGE_REMOVE = frozenset({"remove", "purge", "autoremove", "uninstall"})

SPECIAL: dict[str, Special] = {
    "git": _git,
    "docker": _docker,
    "podman": _docker,
    "systemctl": _systemctl,
    "journalctl": _journalctl,
    "find": _find,
    "crontab": _crontab,
    "ip": _ip,
    "tar": _tar,
    "chmod": _with_flag(frozenset({"-R", "--recursive"}), Level.DETRUIRE, Level.AGIR),
    "rsync": _with_flag(
        frozenset({"--delete", "--delete-after", "--remove-source-files"}),
        Level.DETRUIRE,
        Level.AGIR,
    ),
    "fuser": _with_flag(frozenset({"-k", "--kill"}), Level.DETRUIRE, Level.LIRE),
    "dpkg": _with_flag(
        frozenset({"-i", "-r", "-P", "--install", "--remove", "--purge", "--configure"}),
        Level.AGIR,
        Level.LIRE,
    ),
    "apt": _by_subcommand(PACKAGE_READ, PACKAGE_REMOVE),
    "apt-get": _by_subcommand(PACKAGE_READ, PACKAGE_REMOVE),
    "apt-cache": _by_subcommand(PACKAGE_READ | {"stats", "pkgnames", "madison"}),
    "snap": _by_subcommand(PACKAGE_READ, PACKAGE_REMOVE),
    "flatpak": _by_subcommand(PACKAGE_READ, PACKAGE_REMOVE),
    "pip": _by_subcommand(PACKAGE_READ),
    "pip3": _by_subcommand(PACKAGE_READ),
    "npm": _by_subcommand(PACKAGE_READ | {"audit", "doctor"}),
    "pnpm": _by_subcommand(PACKAGE_READ | {"audit"}),
    "yarn": _by_subcommand(PACKAGE_READ),
    "cargo": _by_subcommand(PACKAGE_READ),
    "uv": _by_subcommand(frozenset({"--version", "version", "tree"})),
    "hostnamectl": _by_subcommand(frozenset({"", "status"})),
    "timedatectl": _by_subcommand(frozenset({"", "status", "show", "list-timezones"})),
    "localectl": _by_subcommand(frozenset({"", "status", "list-locales", "list-keymaps"})),
    "loginctl": _by_subcommand(
        frozenset(
            {
                "",
                "list-sessions",
                "list-users",
                "list-seats",
                "show-session",
                "show-user",
                "session-status",
                "user-status",
            }
        ),
        frozenset({"terminate-session", "terminate-user", "kill-session", "kill-user"}),
    ),
    "gsettings": _by_subcommand(
        frozenset(
            {
                "get",
                "list-schemas",
                "list-keys",
                "list-recursively",
                "range",
                "describe",
                "writable",
                "list-children",
            }
        )
    ),
}
