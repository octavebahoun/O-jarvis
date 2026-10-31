"""Point d'entrée du moteur, lancé par l'app comme processus enfant.

Poignée de main avec l'app :
1. l'app écrit le jeton de session sur l'entrée standard, puis la garde ouverte ;
2. le moteur choisit un port libre sur 127.0.0.1, prépare la base, puis écrit sur
   la sortie standard : `OJARVIS_READY {"port": ..., "pid": ..., "version": ...}` ;
3. quand l'entrée standard se ferme (l'app quitte ou meurt), le moteur s'arrête.

Il publie aussi sa connexion pour le mode shell (`ojarvis.shell.connection`), effacée à
l'arrêt.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import socket
import sys
import threading
import time
from pathlib import Path
from typing import IO

import uvicorn

from ojarvis import __version__
from ojarvis.app import create_app
from ojarvis.config import load_config
from ojarvis.core.logs import configure_logging, get_logger
from ojarvis.shell.connection import (
    EXIT_BAD_TOKEN,
    EXIT_LOCKED,
    READY_PREFIX,
    RUNNING_PREFIX,
    EngineInfo,
    info_path,
    read_info,
    remove_info,
    write_info,
)

log = get_logger("ojarvis.main")


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="ojarvis-engine", description="Moteur d'O-jarvis.")
    parser.add_argument("--dev", action="store_true", help="mode développement")
    parser.add_argument("--port", type=int, default=0, help="0 = port libre choisi par le système")
    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "--token-env",
        metavar="VAR",
        help="lire le jeton dans cette variable d'environnement (développement)",
    )
    source.add_argument(
        "--token-stdin",
        action="store_true",
        default=True,
        help="lire le jeton sur l'entrée standard (défaut)",
    )
    return parser.parse_args(argv)


def acquire_lock(path: Path) -> IO[str] | None:
    """Un seul moteur par dossier de données."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("w")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    handle.write(str(os.getpid()))
    handle.flush()
    return handle


def bind_socket(port: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", port))
    sock.listen(64)
    sock.set_inheritable(True)
    return sock


def watch_parent(server: uvicorn.Server, stream: IO[bytes]) -> None:
    """Arrête le moteur à la fermeture de l'entrée standard ou si le parent disparaît."""
    parent = os.getppid()

    def wait_eof() -> None:
        while stream.read(4096):
            pass
        log.info("stdin_closed")
        server.should_exit = True

    def watch_ppid() -> None:
        while not server.should_exit:
            if os.getppid() != parent:
                log.info("parent_gone")
                server.should_exit = True
                return
            time.sleep(1.0)

    threading.Thread(target=wait_eof, name="stdin-watch", daemon=True).start()
    threading.Thread(target=watch_ppid, name="ppid-watch", daemon=True).start()


def announce_running(connection: Path | None) -> None:
    """Un moteur tourne déjà sur ces données (lancé par un terminal) : sa connexion part vers
    celui qui nous a lancés (l'app), qui le rejoint au lieu d'échouer. Rien s'il n'est pas
    sûr de le lire (voir `read_info`)."""
    running = read_info(connection) if connection is not None else None
    if running is None:
        return
    line = {"port": running.port, "pid": running.pid, "token": running.token}
    sys.stdout.write(RUNNING_PREFIX + json.dumps(line) + "\n")
    sys.stdout.flush()


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["shell"]:
        # Le moteur empaqueté n'a pas de « python -m » : « ojarvis-engine shell » ouvre le
        # terminal de Jarvis.
        from ojarvis.shell.cli import main as shell_main  # noqa: PLC0415

        return shell_main(argv[1:])
    if argv[:1] == ["chrome-host"]:
        # Chrome lance l'hôte de messagerie native de l'extension (voir ojarvis.chrome.host).
        from ojarvis.chrome.host import main as host_main  # noqa: PLC0415

        return host_main(argv[1:])
    args = parse_args(argv)
    config = load_config(dev_mode=args.dev)
    config.paths.ensure()
    configure_logging(
        config.log_level,
        log_file=config.paths.logs_dir / "engine.log",
        json_console=not config.dev_mode,
    )

    if args.token_env:
        token = os.environ.get(args.token_env, "")
        from_stdin = False
    else:
        token = sys.stdin.buffer.readline().decode("utf-8", "replace").strip()
        from_stdin = True
    if len(token) < 32:
        log.error("invalid_token")
        return EXIT_BAD_TOKEN

    lock = acquire_lock(config.paths.lock_path)
    if lock is None:
        log.error("engine_already_running", lock=str(config.paths.lock_path))
        announce_running(info_path(config.paths))
        return EXIT_LOCKED

    sock = bind_socket(args.port)
    port = int(sock.getsockname()[1])
    connection = info_path(config.paths)

    def announce() -> None:
        if connection is not None:
            # Le mode shell rejoint ce moteur (choix validé le 26/10 : voir shell/connection).
            try:
                write_info(connection, EngineInfo(port, os.getpid(), token, __version__))
            except OSError:
                log.warning("shell_connection_not_written")
        info = {"port": port, "pid": os.getpid(), "version": __version__}
        sys.stdout.write(READY_PREFIX + json.dumps(info) + "\n")
        sys.stdout.flush()

    app = create_app(config, token=token, on_ready=announce)
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            log_config=None,
            access_log=False,
            lifespan="on",
            timeout_graceful_shutdown=5,
            ws_ping_interval=20,
        )
    )
    if from_stdin:
        watch_parent(server, sys.stdin.buffer)
    log.info("engine_listening", port=port, dev_mode=config.dev_mode)
    try:
        server.run(sockets=[sock])
    finally:
        if connection is not None:
            remove_info(connection, os.getpid())
        sock.close()
        lock.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
