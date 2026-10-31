"""Découpage d'une ligne de commande comme le ferait bash : mots, opérateurs (`|`, `;`, `&&`),
redirections, heredocs, sous-commandes (`$(…)`) et affectations. Le classement
(`commands.py`) juge ensuite chaque morceau.
"""

from __future__ import annotations

import re
import shlex
from contextvars import ContextVar
from pathlib import Path

from ojarvis.shell.sensitive import _check_sensitive
from ojarvis.shell.verdict import Verdict, _highest
from ojarvis.tools.base import Level

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

#: Ce qui remplace une sous-commande dans la ligne (elle est classée à part).
SUBSTITUTED = "SOUS_COMMANDE"

#: Vrai pendant le classement d'une sous-commande : sa sortie reste au shell.
_CAPTURED: ContextVar[bool] = ContextVar("captured", default=False)

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
