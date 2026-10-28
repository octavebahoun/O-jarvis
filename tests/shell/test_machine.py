"""La carte de la machine : relevée en lecture seule, rangée dans Mémoire, lue au bon moment."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ojarvis.llm.providers.fake import FakeProvider
from ojarvis.services import Services
from ojarvis.shell.machine import MachineMap, collect
from tests.conftest import fake_machine
from tests.shell.test_shell_chat import shell_chat, shell_script
from tests.test_chat import read_sse


@pytest.fixture
def project(services: Services) -> Path:
    assert services.config.user_home is not None
    folder = services.config.user_home / "projet"
    folder.mkdir(parents=True)
    return folder


OUTPUTS = {
    "docker": "Docker version 28.1.1, build 4eba377",
    "node": "v22.11.0",
    "python3": "Python 3.14.0",
    "java": 'openjdk version "21.0.8" 2025-07-15',
    "systemctl": "NetworkManager.service loaded active running Network Manager\n"
    "snapd.service loaded active running Snap Daemon\n"
    "cups.service loaded active running CUPS Scheduler",
}


def fake_run(command: Sequence[str]) -> str | None:
    return OUTPUTS.get(command[0])


def test_the_map_is_read_only_names_sizes_and_versions(tmp_path: Path) -> None:
    proc, etc, home = tmp_path / "proc", tmp_path / "etc", tmp_path / "home"
    proc.mkdir()
    etc.mkdir()
    (proc / "cpuinfo").write_text("model name\t: Intel(R) Core(TM) i5-8250U CPU @ 1.60GHz\n")
    (proc / "meminfo").write_text("MemTotal:        7549920 kB\nSwapTotal:       2097148 kB\n")
    (proc / "mounts").write_text("/dev/sda2 / ext4 rw 0 0\ntmpfs /run tmpfs rw 0 0\n")
    (etc / "os-release").write_text('PRETTY_NAME="Ubuntu 26.04 LTS"\n')
    for project in ("O-jarvis", "Projet/site", ".cache/pas-un-projet"):
        (home / project / ".git").mkdir(parents=True)
    (home / "O-jarvis" / "src").mkdir()
    installed = {"docker", "node", "python3", "java"}
    card = collect(
        home,
        run=fake_run,
        which=lambda name: f"/usr/bin/{name}" if name in installed else None,
        proc=proc,
        etc=etc,
        environ={"XDG_CURRENT_DESKTOP": "ubuntu:GNOME", "XDG_SESSION_TYPE": "wayland"},
    )
    assert card.hardware.startswith("Matériel : processeur Intel(R) Core(TM) i5-8250U")
    assert "mémoire 7,2 Go, échange 2 Go." in card.hardware
    assert card.system.startswith("Système : Ubuntu 26.04 LTS, noyau ")
    assert card.system.endswith("bureau ubuntu, GNOME (wayland).")
    assert card.disks.startswith("Disques : / (ext4) ")  # tmpfs n'est pas un disque
    assert card.tools == "Outils : docker 28.1.1, node 22.11.0, python3 3.14.0, java 21.0.8."
    assert card.services == (
        "Services : 3 en marche, dont snapd, cups, NetworkManager ; arrêtés : docker."
    )
    # Les dossiers cachés (et ce qu'ils contiennent) ne sont pas fouillés.
    assert set(card.projects.split(" : ", 1)[1].rstrip(".").split(", ")) == {
        "~/O-jarvis",
        "~/Projet/site",
    }


def test_the_first_shell_turn_maps_the_machine_once_and_it_is_in_memory(
    client: TestClient, llm: dict[str, FakeProvider], project: Path
) -> None:
    del project
    llm["deepseek"]._script = shell_script("ls")
    first = shell_chat(client, "Bonjour.")
    assert (
        "notice",
        {"text": "Carte de ta machine relevée, en lecture seule : elle est dans Mémoire."},
    ) in first
    lines = client.get("/v1/memory", params={"kind": "machine"}).json()
    assert len(lines) == 6
    assert any(line["content"].startswith("Matériel : ") for line in lines)
    # Une seule fois : le tour suivant la lit sans la relever.
    second = shell_chat(client, "Et maintenant ?", conversation_id=first[0][1]["conversation_id"])
    assert not [e for e, _ in second if e == "notice"]
    audit = client.get("/v1/audit").json()
    assert [i["level"] for i in audit["items"] if i["action"] == "machine.carte"] == [1]
    # En mode shell, la carte est dans le contexte du modèle.
    context = next(d for e, d in second if e == "context")
    assert any(i["kind"] == "machine" for i in context["items"])


def test_the_app_reads_the_map_when_the_question_is_about_the_pc(
    client: TestClient, llm: dict[str, FakeProvider], project: Path
) -> None:
    del project
    llm["deepseek"]._script = shell_script("ls")
    shell_chat(client, "Bonjour.")  # la carte est relevée au premier tour du mode shell

    def machine_lines_kept(text: str) -> int:
        events = read_sse(client.post("/v1/chat", json={"text": text}).text)
        items = next(d for e, d in events if e == "context")["items"]
        return sum(1 for i in items if i["kind"] == "machine" and i["kept"])

    assert machine_lines_kept("Mon PC rame, pourquoi ?") == 6
    assert machine_lines_kept("Raconte-moi une blague.") == 0


def test_the_collection_never_runs_a_level_two_command() -> None:
    # Les seules commandes lancées : les versions des outils et la liste des services.
    seen: list[list[str]] = []

    def watch(command: Sequence[str]) -> str | None:
        seen.append(list(command))
        return None

    collect(Path("/nonexistent"), run=watch, which=lambda name: f"/usr/bin/{name}")
    allowed = {"--version", "-version", "version"}
    for command in seen:
        assert command[0] == "systemctl" or command[1] in allowed, command
    assert any(command[:2] == ["systemctl", "list-units"] for command in seen)


def test_a_map_just_collected_is_not_collected_again(
    client: TestClient, services: Services
) -> None:
    calls: list[Path] = []

    def counting(home: Path) -> MachineMap:
        calls.append(home)
        return fake_machine(home)

    services.machine._collect = counting
    assert client.portal is not None
    client.portal.call(services.machine.ensure)
    lines = client.portal.call(services.machine.refresh)  # juste après : pas de second relevé
    assert len(calls) == 1
    assert lines[0].startswith("Matériel : ")
