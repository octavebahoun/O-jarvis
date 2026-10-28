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
import os
import re
import shlex
from collections.abc import Callable, Iterator
from contextvars import ContextVar
from dataclasses import dataclass
from fnmatch import fnmatch
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
WRITE_REDIRECTS = frozenset({">", ">>", ">|", "&>", "&>>", ">&", "<>"})
HARMLESS_DEVICES = frozenset(
    {"/dev/null", "/dev/stdout", "/dev/stderr", "/dev/zero", "/dev/random", "/dev/urandom"}
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
    "watch": frozenset({"-n", "-d", "--interval"}),
}
SHELLS = frozenset({"bash", "sh", "zsh", "dash", "fish", "ksh"})
#: Ce qui remplace une sous-commande dans la ligne (elle est classée à part).
SUBSTITUTED = "SOUS_COMMANDE"
#: Vrai pendant le classement d'une sous-commande : sa sortie reste au shell.
_CAPTURED: ContextVar[bool] = ContextVar("captured", default=False)
#: Les dossiers où la ligne s'est déplacée (`cd`) : ses chemins relatifs s'y lisent aussi.
_PLACES: ContextVar[tuple[Path, ...]] = ContextVar("places", default=())
#: « NOM=valeur » devant une commande, ou après `env` et `export`.
ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=.*")
#: Variables sans effet sur ce qui est lancé (langue, fuseau, couleurs). Toute autre
#: (`LD_PRELOAD`, `GIT_EXTERNAL_DIFF`, `PATH`, `PAGER`…) peut faire lancer un autre programme.
HARMLESS_VARIABLES = re.compile(
    r"(LANG|LANGUAGE|LC_[A-Z]+|TZ|NO_COLOR|FORCE_COLOR|CLICOLOR|CLICOLOR_FORCE|COLUMNS|LINES"
    r"|TERM)="
)
#: Ouverture d'un heredoc : `<<FIN`, `<<-FIN`, `<<'FIN'`, `<<"FIN"`, `<<\FIN`.
HEREDOC = re.compile(r"<<(-?)[ \t]*(?:'([A-Za-z_]\w*)'|\"([A-Za-z_]\w*)\"|\\?([A-Za-z_]\w*))")
#: Le « {} » et le « \; » de `find -exec` : des mots, que le découpage prendrait pour des
#: opérateurs. Remplacés le temps du découpage.
SEMICOLON, BRACES = "\x1fPV\x1f", "\x1fAC\x1f"
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

# fmt: off
READ_ONLY = frozenset({
    "ls", "ll", "la", "dir", "cat", "tac", "head", "tail", "less", "more", "wc", "nl",
    "grep", "egrep", "fgrep", "rg", "ag", "du", "df", "free", "ps", "pstree", "pgrep",
    "uptime", "w", "who", "whoami", "id", "groups", "uname", "date", "cal", "pwd", "echo",
    "printf", "true", "false", "test", "[", "which", "whereis", "type", "file", "stat",
    "tree", "sort", "uniq", "cut", "paste", "column", "tr", "diff", "cmp", "comm", "md5sum",
    "sha1sum", "sha256sum", "basename", "dirname", "realpath", "readlink", "seq", "sleep",
    "lsblk", "lscpu", "lspci", "lsusb", "lsmod", "lshw", "dmesg", "nproc", "getconf",
    "locale", "printenv", "ss", "netstat", "jq", "xxd",
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
#: Fouille bornée avant une recherche récursive : assez pour trouver le `.env` d'un projet,
#: sans parcourir des milliers de fichiers.
SCAN_DEPTH = 3
SCAN_LIMIT = 5000
SCAN_SKIPPED = frozenset({".git", "node_modules", ".venv", "venv", "__pycache__", ".cache"})
#: Options de rg et ag qui fouillent aussi les fichiers cachés (`.env`).
SEARCH_HIDDEN = frozenset({"--hidden", "-.", "-u", "-uu", "-uuu", "--unrestricted"})


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


def _without_heredocs(text: str) -> str:
    """Retire le texte des « heredocs » (`<<EOF` … `EOF`) : c'est une entrée, pas une commande.

    Seul un `<<` hors guillemets et hors commentaire en ouvre un : après `echo "<<EOF"` ou
    `ls # <<EOF`, les lignes suivantes restent des commandes."""
    kept: list[str] = []
    quote: str | None = None
    lines = text.split("\n")
    index = 0
    while index < len(lines):
        line = lines[index]
        kept.append(line)
        index += 1
        quote, endings = _heredocs_opened(line, quote)
        for ending, strip_tabs in endings:
            while index < len(lines):
                body = lines[index].rstrip("\r")
                index += 1
                if (body.lstrip("\t") if strip_tabs else body) == ending:
                    break
    return "\n".join(kept)


def _heredocs_opened(line: str, quote: str | None) -> tuple[str | None, list[tuple[str, bool]]]:
    """Les heredocs ouverts sur cette ligne (fin, tabulations retirées ?), et les guillemets
    encore ouverts à sa fin."""
    endings: list[tuple[str, bool]] = []
    index, word_start = 0, True
    while index < len(line):
        char = line[index]
        if quote is not None:
            if char == "\\" and quote == '"':
                index += 2
                continue
            if char == quote:
                quote = None
            index += 1
            continue
        if char == "\\":
            index, word_start = index + 2, False
            continue
        if char in "'\"":
            quote, word_start = char, False
            index += 1
            continue
        if char == "#" and word_start:
            break  # un commentaire : bash ne lit rien après
        if line.startswith("<<<", index):
            index, word_start = index + 3, True
            continue
        match = HEREDOC.match(line, index) if line.startswith("<<", index) else None
        if match is not None:
            ending = next(group for group in match.groups()[1:] if group)
            endings.append((ending, match.group(1) == "-"))
            index, word_start = match.end(), False
            continue
        word_start = char in " \t;&|()<>"
        index += 1
    return quote, endings


def _literal_words(text: str) -> str:
    """Protège le « {} », le « \\; » et le « ';' » de `find -exec` : des mots pour bash."""
    out: list[str] = []
    quote: str | None = None
    index = 0
    while index < len(text):
        char = text[index]
        if quote is not None:
            if char == "\\" and quote == '"':
                out.append(text[index : index + 2])
                index += 2
                continue
            if char == quote:
                quote = None
            out.append(char)
            index += 1
            continue
        if char == "\\":
            pair = text[index : index + 2]
            out.append(SEMICOLON if pair == "\\;" else pair)
            index += 2
            continue
        if char in "'\"":
            closing = text.find(char, index + 1)
            inner = text[index + 1 : closing] if closing > index else None
            if inner in (";", "{}"):
                out.append(SEMICOLON if inner == ";" else BRACES)
                index = closing + 1
                continue
            quote = char
        elif text.startswith("{}", index):
            out.append(BRACES)
            index += 2
            continue
        out.append(char)
        index += 1
    return "".join(out)


def _tokens(text: str) -> list[str]:
    flat = _literal_words(_without_heredocs(text)).replace("\\\n", " ")
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
            tokens.append(token.replace(SEMICOLON, "\\;").replace(BRACES, "{}"))
    return tokens


def _split_operators(run: str) -> list[str]:
    parts: list[str] = []
    while run:
        operator = next((op for op in OPERATORS if run.startswith(op)), run[0])
        parts.append(operator)
        run = run[len(operator) :]
    return parts


def _substitutions(text: str) -> tuple[str, list[str]]:
    """Les sous-commandes `$(…)`, `<(…)`, `>(…)` et `` `…` ``, et la ligne où chacune est
    remplacée par un mot neutre : elle n'y est pas classée deux fois. Entre guillemets, bash
    les lance ; entre apostrophes, non (`echo '$('` n'en ouvre aucune)."""
    found: list[str] = []
    line: list[str] = []
    quote: str | None = None
    index = 0
    while index < len(text):
        char = text[index]
        if quote == "'":
            quote = None if char == "'" else quote
            line.append(char)
            index += 1
            continue
        if char == "\\":
            line.append(text[index : index + 2])
            index += 2
            continue
        if char == '"' or (char == "'" and quote is None):
            quote = None if quote == char else char
            line.append(char)
            index += 1
            continue
        if text[index] in "$<>" and text[index + 1 : index + 2] == "(":
            start = index + 2
            depth, end = 1, start
            while end < len(text) and depth:
                depth += {"(": 1, ")": -1}.get(text[end], 0)
                end += 1
            found.append(text[start : end - 1] if depth == 0 else text[start:])
            line.append(SUBSTITUTED)
            index = end
        elif text[index] == "`":
            end = text.find("`", index + 1)
            end = len(text) if end < 0 else end
            found.append(text[index + 1 : end])
            line.append(SUBSTITUTED)
            index = end + 1
        else:
            line.append(text[index])
            index += 1
    return "".join(line), [part for part in found if part.strip()]


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
    while words and ASSIGNMENT.fullmatch(words[0]):
        _check_sensitive(words[0], cwd=cwd, home=home)
        floor = _highest([floor, _assignment(words[0])])
        words = words[1:]
    if not words:
        return floor
    name = Path(words[0]).name
    if name in ADMIN or (name == "machinectl" and words[1:2] in (["shell"], ["login"])):
        raise BlockedTargetError(
            f"Jarvis ne lance jamais « {name} » : il ne voit pas ton mot de passe. Montre la "
            "commande à l'utilisateur pour qu'il la tape lui-même."
        )
    if not _CAPTURED.get() and _prints_secret(name, words[1:]):
        raise BlockedTargetError(
            f"« {' '.join(words[:4])} » affiche un secret : sa sortie partirait vers le modèle. "
            "Utilise-le seulement dans une sous-commande, par exemple "
            '-H "Authorization: Bearer $(gcloud auth print-access-token)".'
        )
    if name in WRAPPERS:
        rest = _skip_options(words[1:], WRAPPERS[name])
        if name == "timeout" and rest and re.fullmatch(r"[0-9.]+[smhd]?", rest[0]):
            rest = rest[1:]
        if name == "env":
            split = [w.split("=", 1)[1] for w in words[1:] if w.startswith("--split-string=")]
            if split or {"-S", "--split-string"} & set(words[1:]):
                # « env -S 'rm -rf x' » : le texte devient une commande.
                inner = _classify(" ".join([*split, *rest]), cwd=cwd, home=home, depth=depth + 1)
                return _highest([floor, Verdict(Level.AGIR, "env -S lance un texte"), inner])
            floor = _highest([floor, *(_assignment(w) for w in rest if ASSIGNMENT.fullmatch(w))])
            rest = [w for w in rest if not ASSIGNMENT.fullmatch(w)]
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
        if words and len(words[-1]) == 1 and words[-1].isdigit():
            # « 2>/dev/null » : le « 2 » est le canal redirigé, pas un argument. Un chiffre
            # ailleurs en est un (« nice -n 5 rm »).
            words.pop()
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
    return words, floor


def _assignment(word: str) -> Verdict:
    """« LANG=C ls » ne change rien ; « LD_PRELOAD=… ls » fait lancer un autre code."""
    if HARMLESS_VARIABLES.match(word):
        return Verdict(Level.LIRE, "réglage de langue ou d'affichage")
    variable = word.split("=", 1)[0]
    return Verdict(Level.AGIR, f"la variable {variable} peut changer le programme lancé")


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


def _check_recursive_search(name: str, args: list[str], *, cwd: Path, home: Path) -> None:
    if not _recursive(name, args):
        return
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
        for found in _secret_files(root, home, skip_hidden=hidden):
            if not any(fnmatch(found.name, pattern) for pattern in excluded):
                raise BlockedTargetError(
                    f"{name} lirait aussi « {found.name} », un fichier de secrets : son contenu "
                    "partirait vers le modèle. Cherche dans un sous-dossier précis, ou exclus-le "
                    f"(--exclude='{found.name}' pour grep, -g '!{found.name}' pour rg)."
                )


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
        deep = len(here.relative_to(root).parts) >= SCAN_DEPTH
        dirs[:] = [
            d
            for d in dirs
            if not deep and d not in SCAN_SKIPPED and not (skip_hidden and d.startswith("."))
        ]
        for name in files:
            seen += 1
            if seen > SCAN_LIMIT:
                return
            if not (skip_hidden and name.startswith(".")) and _is_sensitive(here / name, home):
                yield here / name


def _prints_secret(name: str, args: list[str]) -> bool:
    words = [a for a in args if not a.startswith("-")]
    if name == "kubectl" and words[:2] == ["config", "view"]:
        return "--raw" in args
    if name == "pass":  # `pass` seul liste des noms ; `pass x` affiche un mot de passe
        return bool(words) and words[0] not in ("ls", "list", "find", "search")
    return any(name == p[0] and _in_order(p[1:], words) for p in SECRET_PRINTERS)


def _in_order(wanted: tuple[str, ...], words: list[str]) -> bool:
    """« sts get-session-token » dans « --profile x sts get-session-token » aussi."""
    remaining = iter(words)
    return all(any(word == part for word in remaining) for part in wanted)


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


def _sub(args: list[str], with_value: frozenset[str] = frozenset()) -> tuple[str, list[str]]:
    """La sous-commande (« git status » → « status ») et ce qui la suit."""
    rest = _skip_options(args, with_value)
    return (rest[0], rest[1:]) if rest else ("", [])


# fmt: off
GIT_READ = frozenset({
    "", "status", "log", "diff", "show", "blame", "shortlog", "describe", "rev-parse",
    "rev-list", "ls-files", "ls-tree", "grep", "reflog", "help", "version",
    "whatchanged", "cherry", "count-objects",
})
GIT_BRANCH_LIST = frozenset({"-a", "-r", "-v", "-vv", "--list", "--show-current", "--all"})
#: Options de git avant la sous-commande, suivies d'une valeur.
GIT_OPTIONS = frozenset({"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env"})
# fmt: on


def _git(  # noqa: PLR0911, PLR0912 - une table de décision, lue de haut en bas
    name: str, args: list[str], cwd: Path, home: Path, depth: int
) -> Verdict:
    del name, cwd, home, depth
    after = _skip_options(args, GIT_OPTIONS)
    head = args[: len(args) - len(after)]
    sub, rest = (after[0], after[1:]) if after else ("", [])
    flags = set(rest)
    if any(
        a in ("-c", "--config-env") or a.startswith(("--config-env=", "--exec-path=")) for a in head
    ):
        # « git -c core.fsmonitor='…' status » lance ce programme.
        return Verdict(Level.AGIR, "git -c change une configuration qui peut lancer un programme")
    if sub == "reflog" and rest[:1] in (["expire"], ["delete"]):
        return Verdict(Level.DETRUIRE, f"git reflog {rest[0]} efface l'historique de secours")
    if sub == "ls-remote":
        return Verdict(Level.AGIR, "git ls-remote interroge un serveur")
    if sub in GIT_READ:
        if any(a.startswith(("--output", "--open-files-in-pager", "-O")) for a in rest):
            return Verdict(Level.AGIR, f"git {sub} écrit un fichier ou lance un programme")
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
    starts: list[str] = []
    for arg in args:
        if arg.startswith(("-", "(", "!", ",")):
            break
        starts.append(arg)
    for action in ("-exec", "-execdir", "-ok", "-okdir"):
        if action in args:
            # find seul ne montre que des noms ; avec -exec, il ouvre aussi les fichiers.
            for origin in starts:
                _check_sensitive(origin, cwd=cwd, home=home)
                text = _expand(origin, home)
                if _holds_secrets(Path(text) if text.startswith("/") else cwd / text, home):
                    raise BlockedTargetError(
                        f"find {origin} {action} ouvrirait aussi tes dossiers de secrets. "
                        "Pars d'un dossier de projet précis."
                    )
            start = args.index(action) + 1
            end = next(
                (i for i in range(start, len(args)) if args[i] in ("\\;", ";", "+")), len(args)
            )
            inner = _segment(args[start:end], piped=False, cwd=cwd, home=home, depth=depth)
            return _highest([Verdict(Level.AGIR, f"find {action}"), inner])
    if set(args) & {"-fprint", "-fprint0", "-fprintf", "-fls"}:
        return Verdict(Level.AGIR, "find écrit dans un fichier")
    return Verdict(Level.LIRE, "find cherche")


GCLOUD_READ = frozenset({"list", "describe", "get-value", "info", "version", "help"})
GCLOUD_WRITE = frozenset(
    {"create", "delete", "set", "unset", "update", "enable", "disable", "deploy", "add",
     "remove", "reset", "activate", "login", "revoke", "ssh", "scp", "run", "submit", "import",
     "export", "start", "stop", "resize", "patch", "replace", "move"}
)  # fmt: skip


def _gcloud(name: str, args: list[str], cwd: Path, home: Path, depth: int) -> Verdict:
    del cwd, home, depth
    if "--help" in args or args[:1] == ["help"]:
        return Verdict(Level.LIRE, f"{name} aide")  # l'aide de gcloud ne lance jamais rien
    words = {a for a in args if not a.startswith("-")}
    if words & GCLOUD_READ and not words & GCLOUD_WRITE:
        return Verdict(Level.LIRE, f"{name} lit")
    return Verdict(Level.AGIR, f"{name} agit")


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


#: Options de tar qui lancent un programme (compression, script, commande par fichier).
TAR_PROGRAMS = (
    "--use-compress-program", "--to-command", "--checkpoint-action", "--info-script",
    "--new-volume-script", "--rsh-command", "--rmt-command",
)  # fmt: skip


def _tar(name: str, args: list[str], cwd: Path, home: Path, depth: int) -> Verdict:
    del cwd, home, depth
    first = args[0] if args else ""
    clusters = [a[1:] for a in args if a.startswith("-") and not a.startswith("--")]
    if first and not first.startswith("-"):
        clusters.append(first)  # la forme ancienne : « tar tf a.tar »
    if any(a.startswith(TAR_PROGRAMS) for a in args) or any({"I", "F"} & set(c) for c in clusters):
        return Verdict(Level.AGIR, f"{name} lance un programme (compression ou script)")
    if first in ("-t", "--list") or (first and not first.startswith("--") and "t" in first):
        return Verdict(Level.LIRE, f"{name} liste une archive")
    return Verdict(Level.AGIR, f"{name} crée ou extrait")


def _by_subcommand(read: frozenset[str], destroy: frozenset[str] = frozenset()) -> Special:
    def check(name: str, args: list[str], cwd: Path, home: Path, depth: int) -> Verdict:
        del cwd, home, depth
        sub, rest = _sub(args)
        if sub in read and not (sub == "audit" and "fix" in rest):  # « npm audit fix » installe
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


def _man(name: str, args: list[str], cwd: Path, home: Path, depth: int) -> Verdict:
    del cwd, home, depth
    for arg in args:
        short = arg.startswith("-") and not arg.startswith("--")
        if arg.startswith(("--pager", "--html")) or (short and {"P", "H"} & set(arg[1:])):
            return Verdict(Level.AGIR, f"{name} {arg} lance le programme donné")
    return Verdict(Level.LIRE, f"{name} lit")


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
    "gcloud": _gcloud,
    "docker": _docker,
    "podman": _docker,
    "systemctl": _systemctl,
    "journalctl": _journalctl,
    "find": _find,
    "crontab": _crontab,
    "ip": _ip,
    "tar": _tar,
    "man": _man,
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


# --------------------------------------------------------------------------- « toujours »

#: Commandes à « sous-commande » : le type d'une commande est le programme et sa sous-commande
#: (« npm install », « git commit »).
SUBCOMMANDS = frozenset(
    {"git", "npm", "pnpm", "yarn", "npx", "docker", "podman", "cargo", "uv", "pip", "pip3",
     "make", "go", "dotnet", "deno", "bun", "composer", "bundle", "mvn", "gradle", "flutter",
     "dart", "poetry", "pipx", "systemctl", "snap", "flatpak", "apt", "apt-get"}
)  # fmt: skip
#: Jamais « toujours » : elles parlent au réseau, publient ou agissent dans le cloud. Un
#: contenu piégé pourrait s'en servir pour faire sortir des données : tu valides chaque fois.
NEVER_ALWAYS = frozenset(
    {"curl", "wget", "ssh", "scp", "sftp", "rsync", "nc", "ncat", "netcat", "telnet", "ftp",
     "socat", "gcloud", "aws", "az", "kubectl", "doctl", "heroku", "vercel", "firebase", "gh",
     "git push", "git remote", "git clone", "git fetch", "git pull", "git ls-remote",
     "git submodule", "npm publish", "pnpm publish", "yarn publish", "docker push",
     "docker login", "podman push", "twine", "bash", "sh", "zsh", "eval", "source", ".",
     "xargs", *NETWORK_LOOKUPS}
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
