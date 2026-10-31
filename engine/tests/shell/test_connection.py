"""Le terminal et le moteur : rejoindre celui qui tourne déjà, ou lancer un moteur complet
qui s'annonce à l'app."""

from __future__ import annotations

import io
import json
import os
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from ojarvis.__main__ import announce_running
from ojarvis.__main__ import main as engine_main
from ojarvis.config import resolve_paths
from ojarvis.shell import cli, connection
from ojarvis.shell.connection import (
    EngineInfo,
    info_path,
    python_module,
    read_info,
    remove_info,
    write_info,
)


@pytest.fixture
def runtime(tmp_path: Path) -> dict[str, str]:
    return {"OJARVIS_HOME": str(tmp_path / "donnees"), "XDG_RUNTIME_DIR": str(tmp_path / "run")}


def test_the_connection_is_read_only_when_private_and_alive(runtime: dict[str, str]) -> None:
    path = info_path(resolve_paths(runtime), runtime)
    assert path is not None
    info = EngineInfo(port=4242, pid=os.getpid(), token="j" * 43, version="1.1.0")
    write_info(path, info)
    assert read_info(path) == info
    path.chmod(0o644)  # lisible par d'autres : on n'y touche pas
    assert read_info(path) is None
    path.chmod(0o600)
    write_info(path, EngineInfo(4242, 999_999_999, "j" * 43, "1.1.0"))  # processus disparu
    assert read_info(path) is None


def test_the_connection_is_removed_only_by_its_engine(runtime: dict[str, str]) -> None:
    path = info_path(resolve_paths(runtime), runtime)
    assert path is not None
    write_info(path, EngineInfo(4242, os.getpid(), "j" * 43, "1.1.0"))
    remove_info(path, pid=1)  # un autre moteur : il ne l'efface pas
    assert path.exists()
    remove_info(path, pid=os.getpid())
    assert not path.exists()


def test_two_data_folders_never_share_a_connection(tmp_path: Path) -> None:
    first = {"OJARVIS_HOME": str(tmp_path / "a"), "XDG_RUNTIME_DIR": str(tmp_path / "run")}
    second = {**first, "OJARVIS_HOME": str(tmp_path / "b")}
    assert info_path(resolve_paths(first), first) != info_path(resolve_paths(second), second)
    assert info_path(resolve_paths(first), {}) is None  # sans dossier d'exécution


def test_connect_joins_the_app_engine_or_starts_its_own(
    runtime: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    started: list[bool] = []
    own = EngineInfo(5000, 1, "k" * 43, "1.1.0")

    def fake_start(env: object) -> tuple[EngineInfo, None]:
        del env
        started.append(True)
        return own, None

    monkeypatch.setattr(connection, "start_engine", fake_start)
    monkeypatch.setattr(connection, "reachable", lambda info: True)
    assert connection.connect(runtime) == (own, None)  # rien de publié : il lance le sien
    assert started == [True]

    path = info_path(resolve_paths(runtime), runtime)
    assert path is not None
    app = EngineInfo(4242, os.getpid(), "j" * 43, "1.1.0")
    write_info(path, app)
    assert connection.connect(runtime) == (app, None)  # le moteur de l'app
    assert started == [True]


def test_the_packaged_engine_opens_the_terminal_with_a_subcommand(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Revue du 27/10/2026 : le moteur empaqueté (PyInstaller) refusait « -m ».
    monkeypatch.setattr("sys.frozen", True, raising=False)
    assert python_module("ojarvis.shell.cli", frozen_as="shell")[1:] == ["shell"]
    assert python_module("ojarvis")[1:] == []
    monkeypatch.setattr("sys.frozen", False)
    assert python_module("ojarvis")[1:] == ["-m", "ojarvis"]
    seen: list[list[str]] = []

    def fake_main(argv: list[str]) -> int:
        seen.append(argv)
        return 0

    monkeypatch.setattr(cli, "main", fake_main)
    assert engine_main(["shell", "--dossier", "~/projet"]) == 0
    assert seen == [["--dossier", "~/projet"]]


def test_the_terminal_engine_is_complete_and_announces_itself_to_the_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le moteur lancé par le terminal a la voix et le préchauffage, comme celui de l'app ;
    un second moteur sur les mêmes données donne sa connexion au lieu d'échouer."""
    started: dict[str, Any] = {}

    class Popen:
        def __init__(self, _args: list[str], **kwargs: Any) -> None:
            started.update(kwargs)
            raise OSError("pas de vrai moteur dans les tests")

    monkeypatch.setattr("ojarvis.shell.connection.subprocess.Popen", Popen)
    with pytest.raises(OSError, match="pas de vrai moteur"):
        connection.start_engine({"HOME": str(tmp_path)})
    assert "OJARVIS_SKIP_WARMUP" not in started["env"]
    published = tmp_path / "moteur.json"
    write_info(published, EngineInfo(4242, os.getpid(), "jeton", "1.1.0"))
    printed = io.StringIO()
    monkeypatch.setattr(sys, "stdout", printed)
    announce_running(published)
    line = printed.getvalue()
    assert line.startswith("OJARVIS_RUNNING ")
    assert json.loads(line.removeprefix("OJARVIS_RUNNING ")) == {
        "port": 4242,
        "pid": os.getpid(),
        "token": "jeton",
    }


def test_a_client_rejoins_the_engine_that_runs_now(monkeypatch: pytest.MonkeyPatch) -> None:
    """Le 31/10 : le panneau de Chrome, fermé, arrêtait le moteur que le terminal venait de
    rejoindre (« ConnectError »). La requête repart vers le moteur qui tourne maintenant."""
    seen: list[str] = []

    class Engine(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            seen.append(self.headers["Authorization"])
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args: object) -> None:
            del args

    server = ThreadingHTTPServer(("127.0.0.1", 0), Engine)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    with socket.socket() as probe:  # un port où plus rien n'écoute : le moteur arrêté
        probe.bind(("127.0.0.1", 0))
        gone_port = probe.getsockname()[1]
    now = EngineInfo(server.server_address[1], os.getpid(), "n" * 43, "1.1.0")
    answers = iter([(EngineInfo(gone_port, 1, "g" * 43, "1.1.0"), None), (now, None)])
    monkeypatch.setattr(connection, "connect", lambda env: next(answers))
    link = connection.EngineLink({})
    link.open()
    try:
        with link.client(timeout=5.0) as http:
            assert http.get("/v1/status").status_code == 200
    finally:
        server.shutdown()
    assert seen == [f"Bearer {'n' * 43}"]  # le jeton du moteur qui tourne
    assert link.info == now
