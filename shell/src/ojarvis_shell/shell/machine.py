"""La carte de la machine : ce que Jarvis sait de ton PC, relevé en lecture seule.

Au premier tour du mode shell, puis quand elle a plus d'une semaine, ou à ta demande
(`carte_machine`), Jarvis note le matériel, le système, les disques, les outils installés,
les services en marche et tes dossiers de projets : des noms, des tailles et des versions,
jamais le contenu d'un fichier ni un secret. Rien n'y dépasse la lecture (niveau 1).

La carte est rangée dans Mémoire (type « machine »), où tu la lis, la corriges ou la
supprimes. Le contexte la donne au modèle en mode shell, ou quand ta question parle du PC
(« mon PC rame, pourquoi ? ») : il ne repart pas de zéro à chaque question.
"""

from __future__ import annotations

import asyncio
import math
import os
import platform
import re
import shutil
import subprocess
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from ojarvis.audit.journal import AuditJournal
from ojarvis.core.clock import utc_now
from ojarvis.core.logs import get_logger
from ojarvis.memory.store import MemoryKind, MemoryStore

log = get_logger(__name__)

KIND: MemoryKind = "machine"
SOURCE = "carte de la machine"
#: Au-delà, la carte est relevée de nouveau au premier tour du mode shell.
MAX_AGE = timedelta(days=7)
#: Une carte relevée il y a moins de 2 minutes ne l'est pas de nouveau (le 28/10/2026, le
#: modèle la redemandait juste après le premier relevé).
FRESH = timedelta(minutes=2)
#: Les outils courants et l'option qui donne leur version.
TOOLS: tuple[tuple[str, str], ...] = (
    ("docker", "--version"),
    ("podman", "--version"),
    ("node", "--version"),
    ("npm", "--version"),
    ("pnpm", "--version"),
    ("bun", "--version"),
    ("python3", "--version"),
    ("uv", "--version"),
    ("git", "--version"),
    ("gh", "--version"),
    ("gcloud", "--version"),
    ("rustc", "--version"),
    ("go", "version"),
    ("java", "-version"),
    ("ollama", "--version"),
    ("code", "--version"),
)
#: Services cités par leur nom quand ils tournent (les autres sont seulement comptés).
NOTABLE_SERVICES = (
    "docker",
    "containerd",
    "ollama",
    "postgresql",
    "mysql",
    "mariadb",
    "redis-server",
    "nginx",
    "apache2",
    "ssh",
    "tailscaled",
    "libvirtd",
    "snapd",
    "cups",
    "bluetooth",
    "NetworkManager",
)
REAL_FILESYSTEMS = frozenset(
    {"ext4", "ext3", "btrfs", "xfs", "f2fs", "vfat", "exfat", "ntfs", "ntfs3", "fuseblk", "zfs"}
)
#: Recherche des projets (dossiers git) : profondeur et dossiers bornés.
PROJECT_DEPTH = 3
PROJECT_LIMIT = 15
WALK_LIMIT = 20_000
SKIPPED = frozenset({"node_modules", "venv", "__pycache__", "target", "dist", "build"})
VERSION = re.compile(r"\d+(?:\.\d+)+")

#: Une commande courte en lecture seule : sa sortie, ou None (absente, en échec, trop lente).
Run = Callable[[Sequence[str]], str | None]


@dataclass(frozen=True, slots=True)
class MachineMap:
    hardware: str
    system: str
    disks: str
    tools: str
    services: str
    projects: str

    def lines(self) -> list[str]:
        return [self.hardware, self.system, self.disks, self.tools, self.services, self.projects]


def run_read(command: Sequence[str]) -> str | None:
    try:
        result = subprocess.run(  # noqa: S603 - une liste fixe d'outils, en lecture seule
            list(command),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    text = (result.stdout or result.stderr).strip()
    return text if result.returncode == 0 or text else None


def collect(
    home: Path,
    *,
    run: Run = run_read,
    which: Callable[[str], str | None] = shutil.which,
    proc: Path = Path("/proc"),
    etc: Path = Path("/etc"),
    environ: Mapping[str, str] = os.environ,
) -> MachineMap:
    """Relève la carte, en lecture seule : quelques secondes."""
    tools, installed = _tools(run, which)
    return MachineMap(
        hardware=_hardware(proc),
        system=_system(etc, environ),
        disks=_disks(proc),
        tools=tools,
        services=_services(run, installed),
        projects=_projects(home),
    )


def _size(count: float) -> str:
    """En octets → « 7,2 Go », comme df -h."""
    for unit in ("o", "Ko", "Mo", "Go", "To"):
        if count < 1024 or unit == "To":
            return f"{count:.1f} {unit}".replace(".", ",").replace(",0 ", " ")
        count /= 1024
    return f"{count:.1f} To"


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _hardware(proc: Path) -> str:
    cpu = re.search(r"^model name\s*:\s*(.+)$", _read(proc / "cpuinfo"), re.MULTILINE)
    meminfo = _read(proc / "meminfo")

    def kib(name: str) -> int:
        found = re.search(rf"^{name}:\s*(\d+)", meminfo, re.MULTILINE)
        return int(found.group(1)) * 1024 if found else 0

    name = cpu.group(1).strip() if cpu else (platform.processor() or "inconnu")
    return (
        f"Matériel : processeur {name} ({os.cpu_count() or 0} fils), mémoire "
        f"{_size(kib('MemTotal'))}, échange {_size(kib('SwapTotal'))}."
    )


def _system(etc: Path, environ: Mapping[str, str]) -> str:
    pretty = re.search(r'^PRETTY_NAME="?([^"\n]+)"?$', _read(etc / "os-release"), re.MULTILINE)
    parts = [pretty.group(1) if pretty else "Linux", f"noyau {platform.release()}"]
    desktop = environ.get("XDG_CURRENT_DESKTOP", "").replace(":", ", ")
    session = environ.get("XDG_SESSION_TYPE", "")
    if desktop:
        parts.append(f"bureau {desktop}" + (f" ({session})" if session else ""))
    return "Système : " + ", ".join(parts) + "."


def _disks(proc: Path) -> str:
    seen: set[str] = set()
    parts: list[str] = []
    for line in _read(proc / "mounts").splitlines():
        fields = line.split()
        if len(fields) < 3:
            continue
        device, mount, fstype = fields[0], fields[1].replace("\\040", " "), fields[2]
        if fstype not in REAL_FILESYSTEMS or device in seen or mount.startswith(("/snap", "/run")):
            continue
        seen.add(device)
        try:
            usage = shutil.disk_usage(mount)
        except OSError:
            continue
        # Comme df : la part utilisée de ce qui t'est accessible (la réserve de root à part).
        share = math.ceil(usage.used * 100 / (usage.used + usage.free)) if usage.used else 0
        parts.append(
            f"{mount} ({fstype}) {_size(usage.total)}, {_size(usage.used)} utilisés "
            f"({share} %), {_size(usage.free)} libres"
        )
    return "Disques : " + (" ; ".join(parts) or "aucun lu") + "."


def _tools(run: Run, which: Callable[[str], str | None]) -> tuple[str, set[str]]:
    def probe(tool: tuple[str, str]) -> str | None:
        name, option = tool
        if which(name) is None:
            return None
        lines = (run([name, option]) or "").splitlines()
        version = VERSION.search(lines[0]) if lines else None
        return f"{name} {version.group(0)}" if version else name

    with ThreadPoolExecutor(max_workers=8) as pool:
        found = [entry for entry in pool.map(probe, TOOLS) if entry]
    installed = {entry.split()[0] for entry in found}
    return "Outils : " + (", ".join(found) or "aucun des outils courants") + ".", installed


def _services(run: Run, installed: set[str]) -> str:
    listing = run(
        [
            "systemctl",
            "list-units",
            "--type=service",
            "--state=running",
            "--no-legend",
            "--plain",
            "--no-pager",
        ]
    )
    running = {
        line.split()[0].removesuffix(".service")
        for line in (listing or "").splitlines()
        if line.strip()
    }
    if not running:
        return "Services : liste indisponible."
    notable = [name for name in NOTABLE_SERVICES if name in running]
    text = f"Services : {len(running)} en marche"
    if notable:
        text += ", dont " + ", ".join(notable)
    stopped = [name for name in ("docker", "ollama") if name in installed and name not in running]
    if stopped:
        text += " ; arrêtés : " + ", ".join(stopped)
    return text + "."


def _projects(home: Path) -> str:
    found: list[tuple[float, str]] = []
    visited = 0
    for folder, dirs, _files in os.walk(home):
        here = Path(folder)
        visited += 1
        if visited > WALK_LIMIT:
            break
        if ".git" in dirs and here != home:
            marker = here / ".git" / "index"  # le dernier travail git, sans rien lire dedans
            when = marker.stat().st_mtime if marker.exists() else here.stat().st_mtime
            found.append((when, "~/" + here.relative_to(home).as_posix()))
            dirs[:] = []
            continue
        deep = len(here.relative_to(home).parts) >= PROJECT_DEPTH
        dirs[:] = [d for d in dirs if not deep and not d.startswith(".") and d not in SKIPPED]
    found.sort(reverse=True)
    names = [name for _when, name in found[:PROJECT_LIMIT]]
    more = len(found) - len(names)
    text = ", ".join(names) + (f", et {more} autres" if more > 0 else "")
    return "Projets (dossiers git, les plus récents d'abord) : " + (text or "aucun") + "."


Collect = Callable[[Path], MachineMap]


class MachineCard:
    """La carte rangée dans Mémoire : relevée au besoin, une seule fois à la fois."""

    def __init__(
        self,
        memory: MemoryStore,
        journal: AuditJournal,
        home: Path,
        *,
        collect: Collect = collect,
    ) -> None:
        self._memory = memory
        self._journal = journal
        self._home = home
        self._collect = collect
        self._lock = asyncio.Lock()

    async def lines(self) -> list[str]:
        records = await self._memory.items(kind=KIND, limit=20)
        return [record.content for record in sorted(records, key=lambda r: r.id)]

    async def ensure(self) -> bool:
        """Relève la carte si elle manque ou si elle date : True si elle vient de l'être."""
        async with self._lock:
            records = await self._memory.items(kind=KIND, limit=20)
            if records and utc_now() - max(r.updated_at for r in records) < MAX_AGE:
                return False
            await self._refresh_locked()
            return True

    async def refresh(self) -> list[str]:
        async with self._lock:
            records = await self._memory.items(kind=KIND, limit=20)
            if records and utc_now() - max(r.updated_at for r in records) < FRESH:
                return [record.content for record in sorted(records, key=lambda r: r.id)]
            return await self._refresh_locked()

    async def _refresh_locked(self) -> list[str]:
        card = await asyncio.to_thread(self._collect, self._home)
        for record in await self._memory.items(kind=KIND, limit=50):
            await self._memory.delete(record.id)
        for line in card.lines():
            await self._memory.add(KIND, line, source=SOURCE)
        await self._journal.append(
            actor="jarvis",
            action="machine.carte",
            level=1,
            summary="Carte de la machine relevée, en lecture seule",
            details={"lignes": len(card.lines())},
        )
        log.info("machine_card_collected")
        return card.lines()
