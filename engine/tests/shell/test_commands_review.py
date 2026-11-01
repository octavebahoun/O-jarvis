"""Les failles trouvées par la revue du 1/11/2026 : des commandes qui écrivent ou lancent du
code passaient pour des lectures, et des destructions pour de simples actions."""

from __future__ import annotations

from pathlib import Path

import pytest

from ojarvis.shell.commands import classify
from ojarvis.shell.signatures import signatures
from ojarvis.tools.base import Level
from ojarvis.tools.guards import BlockedTargetError
from ojarvis.tools.sudo import admin_level


@pytest.fixture
def home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    (home / "projet" / "src").mkdir(parents=True)
    (home / ".ssh").mkdir()
    return home


def level(command: str, home: Path) -> Level:
    return classify(command, cwd=home / "projet", home=home).level


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        # Un programme du dossier, même avec --help, ou nommé comme un programme du système.
        ("./deploy.sh --help", Level.AGIR),
        ("./evil --version", Level.AGIR),
        ("./ls", Level.AGIR),
        ("./nice ls", Level.AGIR),
        ("/usr/bin/ls -la", Level.LIRE),
        # version et check ne lisent pas partout.
        ("npm version patch", Level.AGIR),
        ("pnpm version minor", Level.AGIR),
        ("cargo check", Level.AGIR),
        ("uv version --bump patch", Level.AGIR),
        ("pip check", Level.LIRE),
        ("cargo version", Level.LIRE),
        # sort, uniq, xxd peuvent écrire un fichier ou lancer un programme.
        ("sort -o ~/.bashrc /dev/null", Level.AGIR),
        ("sort -ro sorted.txt notes.txt", Level.AGIR),
        ("sort --compress-program=./evil -S1 big.txt", Level.AGIR),
        ("sort -r notes.txt", Level.LIRE),
        ("uniq a.txt ~/.profile", Level.AGIR),
        ("uniq -c a.txt", Level.LIRE),
        ("xxd a.bin b.txt", Level.AGIR),
        ("xxd -l 64 a.bin", Level.LIRE),
        # ip netns exec lance une commande : elle est classée.
        ("ip netns exec x rm -rf build", Level.DETRUIRE),
        ("ip addr", Level.LIRE),
    ],
)
def test_writes_and_launches_are_not_reads(command: str, expected: Level, home: Path) -> None:
    assert level(command, home) == expected


@pytest.mark.parametrize(
    "command",
    [
        r"find . -name x -exec echo {} \; -exec rm -rf {} \;",
        "setsid rm -rf build",
        "watch -n1 'rm -rf build'",
        "flock /tmp/verrou -c 'rm -rf build'",
        "taskset 0x1 rm -rf build",
        "strace -o trace.txt rm -rf build",
        "script -q -c 'rm -rf build' /dev/null",
        "git branch --delete --force main",
        "git branch -df main",
        "git rm -r src",
        "git checkout -f other",
        "git switch --discard-changes main",
    ],
)
def test_destructions_keep_their_double_confirmation(command: str, home: Path) -> None:
    assert level(command, home) == Level.DETRUIRE


@pytest.mark.parametrize(
    ("command", "expected"),
    [("git branch -d fini", Level.AGIR), ("git rm --cached a.txt", Level.AGIR)],
)
def test_milder_git_forms_stay_actions(command: str, expected: Level, home: Path) -> None:
    assert level(command, home) == expected


def test_a_secret_hidden_in_a_watched_text_is_still_seen(home: Path) -> None:
    with pytest.raises(BlockedTargetError):
        level("watch 'cat ~/.ssh/id_rsa'", home)


def test_a_search_too_deep_to_check_needs_approval(home: Path) -> None:
    deep = home / "projet" / "apps" / "web" / "config" / "env"
    deep.mkdir(parents=True)
    (deep / "notes.txt").write_text("rien de secret", encoding="utf-8")
    # Plus profond que ce qui est vérifié : un .env pourrait s'y cacher, tu valides.
    assert level("grep -rn API_KEY .", home) == Level.AGIR
    assert level("grep -rn API_KEY src", home) == Level.LIRE


@pytest.mark.parametrize(
    "command", ["sudo cat /etc/shad*", "sudo cat /etc//shadow", "sudo ls /etc/ssh/*"]
)
def test_sudo_secrets_are_refused_whatever_the_spelling(command: str, home: Path) -> None:
    with pytest.raises(BlockedTargetError, match="fichier de secrets"):
        admin_level(command, home=home)


def test_always_is_never_offered_for_installing_packages(home: Path) -> None:
    for command in ("npm install lodash", "pip install requests", "cargo install ripgrep"):
        assert signatures(command, cwd=home / "projet", home=home) is None
