"""Le fichier de connexion du mode shell : le terminal rejoint le moteur déjà lancé par l'app.

Choix d'Octave (26/10/2026) : le moteur écrit son port et le jeton de session (tiré par la
coque à chaque lancement) dans `$XDG_RUNTIME_DIR/o-jarvis/`, un dossier en mémoire, lisible
par ton seul compte (dossier 0700, fichier 0600). Le fichier change à chaque démarrage et
disparaît à l'arrêt : seul un programme qui tourne déjà sous ton compte peut le lire.

Son nom porte l'empreinte du dossier de données : un moteur d'essai (`OJARVIS_HOME`) n'écrase
jamais celui de l'app, et le terminal trouve le moteur des mêmes données que lui.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import secrets
import stat
import subprocess
import sys
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from ojarvis.config import APP_DIR_NAME, Paths, resolve_paths

#: Poignée de main du moteur (voir `ojarvis.__main__`), lue aussi par le terminal.
READY_PREFIX = "OJARVIS_READY "
#: Un moteur tourne déjà (lancé par un terminal) : sa connexion, pour que l'app le rejoigne.
RUNNING_PREFIX = "OJARVIS_RUNNING "
EXIT_LOCKED = 3
EXIT_BAD_TOKEN = 4


@dataclass(frozen=True, slots=True)
class EngineInfo:
    port: int
    pid: int
    token: str
    version: str

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


def info_path(paths: Paths, env: Mapping[str, str] | None = None) -> Path | None:
    """Où ce moteur publie sa connexion ; None sans dossier d'exécution (`XDG_RUNTIME_DIR`)."""
    env = os.environ if env is None else env
    runtime = env.get("XDG_RUNTIME_DIR")
    if not runtime:
        return None
    digest = hashlib.sha256(str(paths.data_dir.resolve()).encode()).hexdigest()[:12]
    return Path(runtime) / APP_DIR_NAME / f"moteur-{digest}.json"


def write_info(path: Path, info: EngineInfo) -> None:
    """Écrit d'un coup (fichier temporaire puis renommage), lisible par toi seul."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    temporary = path.with_name(f".{path.name}.{info.pid}")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(
            {"port": info.port, "pid": info.pid, "token": info.token, "version": info.version},
            handle,
        )
    temporary.replace(path)


def read_info(path: Path) -> EngineInfo | None:
    """Le moteur publié, s'il est sûr de le lire : un fichier à toi, que personne d'autre ne
    peut lire, et un processus encore vivant. Sinon None (le terminal lancera son moteur)."""
    try:
        status = path.stat()
    except OSError:
        return None
    if status.st_uid != os.getuid() or status.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        info = EngineInfo(
            port=int(data["port"]),
            pid=int(data["pid"]),
            token=str(data["token"]),
            version=str(data.get("version", "")),
        )
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return info if _alive(info.pid) else None


def remove_info(path: Path, pid: int) -> None:
    """Efface la connexion à l'arrêt, seulement si c'est bien la sienne."""
    info = read_info(path)
    if info is None or info.pid == pid:
        with contextlib.suppress(OSError):
            path.unlink(missing_ok=True)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def python_module(module: str, *, frozen_as: str | None = None) -> list[str]:
    """Lancer un module d'O-jarvis avec ce même Python. Le moteur empaqueté (PyInstaller)
    n'a pas de « -m » : le terminal y est la sous-commande `shell`, le moteur le programme
    seul."""
    if getattr(sys, "frozen", False):
        return [sys.executable, frozen_as] if frozen_as else [sys.executable]
    return [sys.executable, "-m", module]


class EngineUnavailableError(Exception):
    pass


def reachable(info: EngineInfo) -> bool:
    try:
        response = httpx.get(
            f"{info.url}/v1/status",
            headers={"Authorization": f"Bearer {info.token}"},
            timeout=3.0,
        )
    except httpx.HTTPError:
        return False
    return response.status_code == 200


def start_engine(
    env: Mapping[str, str], *, timeout_s: float = 180.0
) -> tuple[EngineInfo, subprocess.Popen[bytes]]:
    """Lance le moteur complet, comme le fait l'app (voix comprise) : jeton sur l'entrée
    standard, gardée ouverte ; il s'arrête quand le terminal se ferme. L'app ouverte ensuite
    le rejoint au lieu d'en lancer un second."""
    token = secrets.token_urlsafe(32)
    process = subprocess.Popen(  # noqa: S603 - notre propre moteur, sans entrée extérieure
        python_module("ojarvis"),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=dict(env),
        start_new_session=True,  # Ctrl+C dans le terminal ne coupe pas le moteur
    )
    stdin, stdout = process.stdin, process.stdout
    if stdin is None or stdout is None:
        process.kill()
        raise EngineUnavailableError("Le moteur n'a pas de tubes d'entrée et de sortie.")
    stdin.write(token.encode() + b"\n")
    stdin.flush()
    ready: dict[str, Any] = {}

    def read_ready() -> None:
        for raw in stdout:
            line = raw.decode("utf-8", "replace")
            if line.startswith(READY_PREFIX) and not ready:
                ready.update(json.loads(line[len(READY_PREFIX) :]))
            # La suite est lue et jetée : un tube plein bloquerait le moteur.

    threading.Thread(target=read_ready, name="engine-stdout", daemon=True).start()
    deadline = time.monotonic() + timeout_s
    while not ready:
        if process.poll() is not None:
            if process.returncode == EXIT_LOCKED:
                raise EngineUnavailableError(
                    "Le moteur de l'app tourne déjà mais ne publie pas sa connexion : "
                    "redémarre l'app (version à jour) ou ferme-la, puis relance."
                )
            raise EngineUnavailableError(
                f"Le moteur n'a pas démarré (code {process.returncode}) : voir son journal, "
                "~/.local/state/o-jarvis/logs/engine.log."
            )
        if time.monotonic() > deadline:
            process.kill()
            raise EngineUnavailableError("Le moteur met trop de temps à démarrer.")
        time.sleep(0.1)
    info = EngineInfo(int(ready["port"]), int(ready["pid"]), token, str(ready.get("version")))
    return info, process


def connect(env: Mapping[str, str]) -> tuple[EngineInfo, subprocess.Popen[bytes] | None]:
    """Le moteur de l'app s'il tourne, sinon un moteur à ce terminal."""
    path = info_path(resolve_paths(env), env)
    if path is not None:
        info = read_info(path)
        if info is not None and reachable(info):
            return info, None
    return start_engine(env)


def stop_engine(process: subprocess.Popen[bytes] | None) -> None:
    if process is None:
        return
    if process.stdin is not None:
        with contextlib.suppress(OSError):
            process.stdin.close()  # le moteur s'arrête à la fermeture de son entrée
    try:
        process.wait(15)
    except subprocess.TimeoutExpired:
        process.kill()


class EngineLink:
    """Le moteur qu'utilise ce client (terminal, panneau de Chrome), et de quoi en retrouver
    un s'il s'arrête : le moteur rejoint appartient peut-être à l'app ou au panneau qu'on
    vient de fermer. On rejoint alors celui qui tourne, sinon on en lance un."""

    def __init__(self, env: Mapping[str, str]) -> None:
        self._env = env
        self._lock = threading.Lock()
        #: Les moteurs lancés par ce client : arrêtés à sa fermeture.
        self.started: list[subprocess.Popen[bytes]] = []
        self.info: EngineInfo | None = None

    def open(self) -> subprocess.Popen[bytes] | None:
        """Rejoint ou lance un moteur ; rend le processus s'il a fallu le lancer."""
        info, process = connect(self._env)
        if process is not None:
            self.started.append(process)
        self.info = info
        return process

    def rejoin(self, gone: EngineInfo) -> EngineInfo:
        with self._lock:  # plusieurs requêtes peuvent échouer en même temps : un seul relais
            info = self.info
            if info is None or info == gone:
                self.open()
                info = self.info
            if info is None:
                raise EngineUnavailableError("Aucun moteur à rejoindre.")
            return info

    def client(self, *, timeout: float) -> httpx.Client:
        info = self.info
        if info is None:
            raise EngineUnavailableError("Aucun moteur rejoint.")
        return httpx.Client(
            base_url=info.url,
            headers={"Authorization": f"Bearer {info.token}"},
            timeout=timeout,
            transport=_Rejoining(self),
        )

    def close(self) -> None:
        for process in self.started:
            stop_engine(process)


class _Rejoining(httpx.BaseTransport):
    """Une connexion refusée : la requête n'est jamais arrivée. Elle repart une fois, vers le
    moteur qui tourne maintenant."""

    def __init__(self, link: EngineLink) -> None:
        self._link = link
        self._inner = httpx.HTTPTransport()

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        try:
            return self._inner.handle_request(request)
        except httpx.ConnectError:
            gone = self._link.info
            if gone is None or request.url.port != gone.port:
                raise
            info = self._link.rejoin(gone)
            headers = httpx.Headers(request.headers)
            headers["Authorization"] = f"Bearer {info.token}"
            headers["Host"] = f"127.0.0.1:{info.port}"
            retry = httpx.Request(
                request.method,
                request.url.copy_with(port=info.port),
                headers=headers,
                content=request.read(),
                extensions=request.extensions,
            )
            return self._inner.handle_request(retry)

    def close(self) -> None:
        self._inner.close()
