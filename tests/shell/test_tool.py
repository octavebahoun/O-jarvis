"""L'outil executer_commande : vrai lancement, délai, sortie tronquée, environnement propre."""

from __future__ import annotations

import time
from pathlib import Path

import httpx
import pytest

from ojarvis.tools import shell
from ojarvis.tools.base import Level, ToolContext, ToolRegistry
from ojarvis.tools.guards import BlockedTargetError
from ojarvis.tools.shell import RunCommand, clean_environment


@pytest.fixture
def home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    (home / "projet").mkdir(parents=True)
    return home


def context(home: Path) -> ToolContext:
    return ToolContext(http=httpx.AsyncClient(), secret=lambda _name: None, home=home)


async def run(
    home: Path, command: str, *, delai_s: int = 20, environ: dict[str, str] | None = None
) -> str:
    tool = RunCommand(home, environ=environ or {"PATH": "/usr/bin:/bin", "HOME": str(home)})
    args = tool.parse({"commande": command, "dossier": "~/projet", "delai_s": delai_s})
    output = await tool.run(args, context(home))
    assert output.external  # la sortie est une donnée, jamais une instruction
    return output.text


async def test_a_command_runs_in_the_session_folder(home: Path) -> None:
    text = await run(home, "pwd && echo bonjour && echo oups >&2")
    assert "code de sortie 0" in text
    assert str(home / "projet") in text
    assert "--- sortie ---\n" in text
    assert "bonjour" in text
    assert "--- erreurs ---\noups" in text


async def test_a_failing_command_reports_its_code(home: Path) -> None:
    assert "code de sortie 3" in await run(home, "exit 3")


async def test_a_command_waiting_for_input_does_not_block(home: Path) -> None:
    start = time.monotonic()
    text = await run(home, 'read reponse; echo "lu:$reponse"')
    assert "lu:" in text
    assert time.monotonic() - start < 5


async def test_the_deadline_stops_the_whole_process_group(home: Path) -> None:
    start = time.monotonic()
    text = await run(home, "sleep 30 & sleep 30", delai_s=1)
    assert "arrêtée après 1 s (délai dépassé)" in text
    assert time.monotonic() - start < 8


async def test_a_background_process_holding_the_output_is_stopped(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(shell, "DRAIN_S", 0.3)
    start = time.monotonic()
    text = await run(home, "echo parti; sleep 30 &")
    assert "code de sortie 0" in text
    assert "parti" in text
    assert time.monotonic() - start < 5


async def test_a_long_output_keeps_its_beginning_and_its_end(home: Path) -> None:
    text = await run(home, "seq 1 200000")
    assert "octets coupés" in text
    assert "\n1\n2\n3\n" in text
    assert text.rstrip().endswith("200000")
    assert len(text) < 14_000


async def test_secrets_are_removed_from_the_environment(home: Path) -> None:
    environ = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(home),
        "DEEPSEEK_API_KEY": "sk-secret",
        "GITHUB_TOKEN": "ghp-secret",
        "DB_PASSWORD": "motdepasse",
        "OJARVIS_ENGINE_TOKEN": "jeton-du-moteur",
        "LANG": "fr_FR.UTF-8",
    }
    text = await run(home, "printenv", environ=environ)
    for secret in ("sk-secret", "ghp-secret", "motdepasse", "jeton-du-moteur"):
        assert secret not in text
    assert "LANG=fr_FR.UTF-8" in text
    assert "GIT_TERMINAL_PROMPT=0" in text  # jamais de question qui bloquerait


def test_clean_environment_keeps_what_commands_need() -> None:
    env = clean_environment({"PATH": "/bin", "SSH_AUTH_SOCK": "/run/agent", "AWS_SECRET": "x"})
    assert env["PATH"] == "/bin"
    assert env["SSH_AUTH_SOCK"] == "/run/agent"
    assert "AWS_SECRET" not in env
    assert env["PAGER"] == "cat"


def test_the_level_comes_from_the_command(home: Path) -> None:
    tool = RunCommand(home)

    def level(command: str) -> Level:
        return tool.level_for(tool.parse({"commande": command, "dossier": "~/projet"}))

    assert level("git status") == Level.LIRE
    assert level("npm install") == Level.AGIR
    assert level("rm -rf build") == Level.DETRUIRE
    with pytest.raises(BlockedTargetError, match="mot de passe"):
        level("sudo reboot")
    describe = tool.describe(tool.parse({"commande": "ls", "dossier": "~/projet"}))
    assert describe == "Lancer « ls » dans ~/projet"


def test_the_folder_must_be_in_the_home_and_not_a_secret_one(home: Path) -> None:
    tool = RunCommand(home)
    for folder in ("/etc", "~/.ssh"):
        with pytest.raises(BlockedTargetError):
            tool.level_for(tool.parse({"commande": "ls", "dossier": folder}))


def test_the_tool_is_only_offered_in_shell_mode(home: Path) -> None:
    registry = ToolRegistry([RunCommand(home)])
    assert registry.specs(max_level=Level.DETRUIRE) == []
    shell_specs = registry.specs(max_level=Level.DETRUIRE, shell_mode=True)
    assert [s.name for s in shell_specs] == ["executer_commande"]
