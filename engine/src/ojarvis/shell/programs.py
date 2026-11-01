"""Règles par programme du classement des commandes : git, docker, systemctl, find, gcloud…

Chaque règle dit ce que fait une commande d'après ses sous-commandes et ses options : lire
(niveau 1), agir (2) ou détruire (3). Le classement général est dans `commands.py`.
"""

from __future__ import annotations

from pathlib import Path

from ojarvis.shell.sensitive import _check_sensitive, _expand, _holds_secrets
from ojarvis.shell.verdict import Special, Verdict, _highest, _skip_options, _sub
from ojarvis.tools.base import Level
from ojarvis.tools.guards import BlockedTargetError

# fmt: off
GIT_READ = frozenset({
    "", "status", "log", "diff", "show", "blame", "shortlog", "describe", "rev-parse",
    "rev-list", "ls-files", "ls-tree", "grep", "reflog", "help", "version",
    "whatchanged", "cherry", "count-objects",
})

GIT_BRANCH_LIST = frozenset({"-a", "-r", "-v", "-vv", "--list", "--show-current", "--all"})

#: Options de git avant la sous-commande, suivies d'une valeur.
GIT_OPTIONS = frozenset({"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env"})


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
    if sub in ("checkout", "restore", "switch") and (
        (sub != "switch" and ("--" in rest or "." in rest))
        or (sub == "restore" and "--staged" not in flags)
        or flags & {"-f", "--force", "--discard-changes"}
    ):
        return Verdict(Level.DETRUIRE, f"git {sub} efface des changements locaux")
    if sub == "rm" and "--cached" not in flags:
        return Verdict(Level.DETRUIRE, "git rm supprime des fichiers")
    if sub == "branch":
        letters = {c for a in rest if a.startswith("-") and not a.startswith("--") for c in a[1:]}
        forced = "f" in letters or "--force" in flags
        deletes = "D" in letters or (("d" in letters or "--delete" in flags) and forced)
        if deletes:
            return Verdict(Level.DETRUIRE, "git branch supprime une branche de force")
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
    actions = [i for i, a in enumerate(args) if a in ("-exec", "-execdir", "-ok", "-okdir")]
    if actions:
        # find seul ne montre que des noms ; avec -exec, il ouvre aussi les fichiers.
        for origin in starts:
            _check_sensitive(origin, cwd=cwd, home=home)
            text = _expand(origin, home)
            if _holds_secrets(Path(text) if text.startswith("/") else cwd / text, home):
                raise BlockedTargetError(
                    f"find {origin} {args[actions[0]]} ouvrirait aussi tes dossiers de secrets. "
                    "Pars d'un dossier de projet précis."
                )
        # Le classement général, importé ici : commands.py importe ces règles.
        from ojarvis.shell.commands import _segment  # noqa: PLC0415

        # Chaque clause compte : « -exec echo {} \; -exec rm -rf {} \; » supprime.
        verdicts = [Verdict(Level.AGIR, f"find {args[actions[0]]}")]
        for index in actions:
            end = next(
                (i for i in range(index + 1, len(args)) if args[i] in ("\\;", ";", "+")),
                len(args),
            )
            verdicts.append(
                _segment(args[index + 1 : end], piped=False, cwd=cwd, home=home, depth=depth)
            )
        return _highest(verdicts)
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
    if "exec" in args:
        # « ip netns exec ns cmd », « ip vrf exec vrf cmd » : cmd est lancée, on la classe.
        from ojarvis.shell.commands import _segment  # noqa: PLC0415

        inner = args[args.index("exec") + 2 :]
        verdicts = [Verdict(Level.AGIR, f"{name} exec lance une commande")]
        if inner:
            verdicts.append(_segment(inner, piped=False, cwd=cwd, home=home, depth=depth))
        return _highest(verdicts)
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



def _sort(name: str, args: list[str], cwd: Path, home: Path, depth: int) -> Verdict:
    """sort lit, sauf « -o fichier » (il l'écrit) et « --compress-program » (il le lance)."""
    del cwd, home, depth
    for arg in args:
        short = arg.startswith("-") and not arg.startswith("--")
        if arg.startswith(("--output", "--compress-program")) or (short and "o" in arg[1:]):
            return Verdict(Level.AGIR, f"{name} {arg} écrit un fichier ou lance un programme")
    return Verdict(Level.LIRE, f"{name} lit")


def _output_file(values: frozenset[str]) -> Special:
    """uniq, xxd : un second fichier nommé est leur sortie, qu'ils écrasent."""

    def check(name: str, args: list[str], cwd: Path, home: Path, depth: int) -> Verdict:
        del cwd, home, depth
        files = [a for a in _skip_options(args, values) if not a.startswith("-")]
        if len(files) >= 2:
            return Verdict(Level.AGIR, f"{name} écrit dans {files[1]}")
        return Verdict(Level.LIRE, f"{name} lit")

    return check

# fmt: off
PACKAGE_READ = frozenset({
    "list", "ls", "show", "info", "search", "find", "outdated", "view", "why", "freeze",
    "policy", "depends", "rdepends", "changelog", "connections", "services",
    "changes", "history", "remotes", "ps", "tree", "metadata", "--version", "-v",
})
# « npm version patch » change la version et lance les scripts du projet ; « cargo check »
# lance build.rs : pas des lectures. « pip check » et « cargo version », si.

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
    "sort": _sort,
    "uniq": _output_file(
        frozenset({"-f", "-s", "-w", "--skip-fields", "--skip-chars", "--check-chars"})
    ),
    "xxd": _output_file(frozenset({"-c", "-g", "-l", "-s", "-o", "-n", "-cols", "-len", "-seek"})),
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
    "pip": _by_subcommand(PACKAGE_READ | {"check"}),
    "pip3": _by_subcommand(PACKAGE_READ | {"check"}),
    "npm": _by_subcommand(PACKAGE_READ | {"audit", "doctor"}),
    "pnpm": _by_subcommand(PACKAGE_READ | {"audit"}),
    "yarn": _by_subcommand(PACKAGE_READ),
    "cargo": _by_subcommand(PACKAGE_READ | {"version"}),
    "uv": _by_subcommand(frozenset({"--version", "tree"})),
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
