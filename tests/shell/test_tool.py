"""L'outil executer_commande : vrai lancement, délai, sortie tronquée, environnement propre."""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import sys
import time
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from ojarvis.core.errors import NotFoundError
from ojarvis.shell import cli
from ojarvis.tools.base import Level, ToolContext, ToolRegistry
from ojarvis.tools.guards import BlockedTargetError
from ojarvis.tools.shell import OpenShell, RunCommand, clean_environment


@pytest.fixture
def home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    (home / "projet").mkdir(parents=True)
    return home


def context(home: Path) -> ToolContext:
    return ToolContext(http=httpx.AsyncClient(), secret=lambda _name: None, home=home)


def tool_for(home: Path, environ: dict[str, str] | None = None) -> RunCommand:
    environ = environ or {"PATH": "/usr/bin:/bin", "HOME": str(home)}
    return RunCommand(home, log_dir=home / "journaux", environ=environ)


async def run(
    home: Path,
    command: str,
    *,
    delai_s: int = 20,
    environ: dict[str, str] | None = None,
    background: bool = False,
) -> str:
    tool = tool_for(home, environ)
    args = tool.parse(
        {
            "commande": command,
            "dossier": "~/projet",
            "delai_s": delai_s,
            "arriere_plan": background,
        }
    )
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


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def stop(pid: int) -> None:
    with contextlib.suppress(ProcessLookupError):
        os.killpg(pid, signal.SIGKILL)


async def test_a_process_left_attached_to_the_output_is_stopped_at_the_deadline(
    home: Path,
) -> None:
    start = time.monotonic()
    text = await run(home, "echo parti; sleep 30 &", delai_s=1)
    assert "code de sortie 0" in text
    assert "parti" in text
    assert "arriere_plan=true" in text  # la piste pour un serveur
    assert time.monotonic() - start < 5


async def test_a_well_detached_server_keeps_running(home: Path) -> None:
    """Le 26/10, un Vite lancé avec nohup et une redirection a été tué par l'ancien délai de
    2 s : un serveur dont la sortie va dans un fichier n'est plus jamais arrêté."""
    log = home / "serveur.log"
    start = time.monotonic()
    text = await run(home, f"nohup sleep 30 > {log} 2>&1 & echo $!")
    pid = int(text.split("--- sortie ---\n")[1].split()[0])
    try:
        assert time.monotonic() - start < 5
        await asyncio.sleep(0.3)
        assert alive(pid)
    finally:
        stop(pid)


async def test_a_background_command_is_detached_with_its_journal(home: Path) -> None:
    script = "import time; print('pret', flush=True); time.sleep(30)"
    text = await run(home, f'python3 -c "{script}"', background=True)
    assert "lancée en arrière-plan : PID" in text
    pid = int(text.split("PID ")[1].split(",")[0])
    try:
        assert "pret" in text  # le début du journal
        assert "journal ~/journaux/" in text
        assert alive(pid)
        journal = next((home / "journaux").glob("*.log"))
        assert journal.stat().st_mode & 0o777 == 0o600
    finally:
        stop(pid)


async def test_a_background_command_that_fails_at_once_says_so(home: Path) -> None:
    text = await run(home, "echo port déjà pris; exit 3", background=True)
    assert "arrêtée tout de suite (code de sortie 3)" in text
    assert "port déjà pris" in text


def test_the_description_says_in_background(home: Path) -> None:
    tool = tool_for(home)
    args = tool.parse({"commande": "npm run dev", "dossier": "~/projet", "arriere_plan": True})
    assert tool.describe(args) == "Lancer en arrière-plan « npm run dev » dans ~/projet"


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


def test_commands_never_use_the_engine_own_python() -> None:
    venv = "/home/test/O-jarvis/engine/.venv"
    env = clean_environment(
        {"PATH": f"{venv}/bin:/usr/local/bin:/usr/bin", "VIRTUAL_ENV": venv},
        engine_bin=f"{venv}/bin",
    )
    assert env["PATH"] == "/usr/local/bin:/usr/bin"
    assert "VIRTUAL_ENV" not in env


def test_clean_environment_keeps_what_commands_need() -> None:
    env = clean_environment({"PATH": "/bin", "SSH_AUTH_SOCK": "/run/agent", "AWS_SECRET": "x"})
    assert env["PATH"] == "/bin"
    assert env["SSH_AUTH_SOCK"] == "/run/agent"
    assert "AWS_SECRET" not in env
    assert env["PAGER"] == "cat"


def test_the_level_comes_from_the_command(home: Path) -> None:
    tool = tool_for(home)

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
    tool = tool_for(home)
    for folder in ("/etc", "~/.ssh"):
        with pytest.raises(BlockedTargetError):
            tool.level_for(tool.parse({"commande": "ls", "dossier": folder}))


def test_the_tool_is_only_offered_in_shell_mode(home: Path) -> None:
    registry = ToolRegistry([tool_for(home)])
    assert registry.specs(max_level=Level.DETRUIRE) == []
    shell_specs = registry.specs(max_level=Level.DETRUIRE, shell_mode=True)
    assert [s.name for s in shell_specs] == ["executer_commande"]


# --------------------------------------------------------------------------- ouvrir le mode shell


def only(*installed: str) -> Callable[[str], str | None]:
    return lambda name: f"/usr/bin/{name}" if name in installed else None


async def test_open_shell_starts_the_user_terminal_in_the_folder(home: Path) -> None:
    launched: list[list[str]] = []
    tool = OpenShell(home, environ={}, which=only("ptyxis"), launch=launched.append)
    output = await tool.run(tool.parse({"dossier": "~/projet"}), context(home))
    assert launched == [
        [
            "/usr/bin/ptyxis",
            "--new-window",
            "--",
            sys.executable,
            "-m",
            "ojarvis.shell.cli",
            "--dossier",
            str(home / "projet"),
        ]
    ]
    assert output.text == "Mode shell ouvert dans ~/projet (ptyxis) : Jarvis t'y attend."
    assert tool.level == Level.LIRE


async def test_the_preferred_terminal_comes_first_and_the_data_folder_follows(
    home: Path,
) -> None:
    launched: list[list[str]] = []
    environ = {"OJARVIS_HOME": "/donnees/essai", "XDG_RUNTIME_DIR": "/run/user/1000", "X": "y"}
    tool = OpenShell(
        home, environ=environ, which=only("ptyxis", "xdg-terminal-exec"), launch=launched.append
    )
    await tool.run(tool.parse({}), context(home))
    command = launched[0]
    assert command[0] == "/usr/bin/xdg-terminal-exec"
    # Un terminal déjà ouvert ignore notre environnement : `env` porte ce qu'il faut.
    assert command[1:4] == ["env", "OJARVIS_HOME=/donnees/essai", "XDG_RUNTIME_DIR=/run/user/1000"]
    assert "X=y" not in command
    assert command[-2:] == ["--dossier", str(home)]


async def test_open_shell_refuses_secret_folders_and_needs_a_terminal(home: Path) -> None:
    tool = OpenShell(home, environ={}, which=only(), launch=lambda _c: None)
    with pytest.raises(NotFoundError, match="Aucun terminal"):
        await tool.run(tool.parse({}), context(home))
    (home / ".ssh").mkdir()
    with pytest.raises(BlockedTargetError):
        await tool.run(tool.parse({"dossier": "~/.ssh"}), context(home))
    with pytest.raises(NotFoundError, match="introuvable"):
        await tool.run(tool.parse({"dossier": "~/inexistant"}), context(home))


def test_open_shell_is_offered_in_the_app(home: Path) -> None:
    registry = ToolRegistry([tool_for(home), OpenShell(home)])
    assert [s.name for s in registry.specs(max_level=Level.DETRUIRE)] == ["ouvrir_mode_shell"]


def test_the_client_refuses_a_folder_outside_the_home(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("OJARVIS_USER_HOME", str(home))
    assert cli.main(["--dossier", "/etc"]) == 2
    assert cli.main(["--dossier", "~/inexistant"]) == 2
    assert "Dossier introuvable" in capsys.readouterr().out
