"""O-jarvis Shell, branché sur O-jarvis : ce que le hackathon Meta a construit.

Le moteur d'O-jarvis appelle ce module en un seul point (`ojarvis.extension`) et lui prête ses
services. Il y ajoute :

- le mode shell : le classement des commandes, `executer_commande`, sudo lancé par toi,
  `voir_page`, la carte de la machine, la mémoire des impasses, la retouche de fichier relue
  par un autre modèle ;
- Jarvis dans Chrome : le pont vers l'extension, les outils d'onglet et leur secours visuel ;
- l'écran : trouver un élément (SAM 3.1 et Muse Spark), cliquer dessus, le partage en direct,
  les extraits filmés ;
- les modèles de Meta : le profil « Tout Meta », Muse Voice Transcribe, les voix MMS.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter
from ojarvis.context.engine import RELEVANCE_FLOOR
from ojarvis.extension import Added, Lent
from ojarvis.local_models import LOCAL_MODELS, ModelSet
from ojarvis.tools.base import Tool
from ojarvis.tools.code import ListCapabilities
from ojarvis.tools.screen import LookAtScreen
from ojarvis.voice.model_sets import META_MODEL_SETS
from ojarvis.voice.tts import FallbackTTS
from ojarvis.voice.vad import EnergyVad

from ojarvis_shell.agent.review import CodeReviewer
from ojarvis_shell.api import routes_chrome, routes_screen
from ojarvis_shell.chrome.bridge import ChromeBridge
from ojarvis_shell.desktop.film import Filmer
from ojarvis_shell.desktop.locate import Locator, ScreenMarks
from ojarvis_shell.desktop.pointer import PortalPointer
from ojarvis_shell.desktop.sam import SamClient
from ojarvis_shell.desktop.screencast import ScreenShare, live_or_capture
from ojarvis_shell.llm import meta_profile  # noqa: F401 - le profil « Tout Meta » s'inscrit
from ojarvis_shell.shell.machine import MachineCard
from ojarvis_shell.shell.servers import BackgroundSessions
from ojarvis_shell.shell.signatures import talks_to_network as _talks_to_network
from ojarvis_shell.tools.app_click import ClickInApp
from ojarvis_shell.tools.edit import EditFile
from ojarvis_shell.tools.impasses import impasse_tools
from ojarvis_shell.tools.machine import ReadMachineMap
from ojarvis_shell.tools.page_view import LookAtPage
from ojarvis_shell.tools.screen_actions import BringWindow, ClickOnScreen, FilmScreen, FindOnScreen
from ojarvis_shell.tools.shell import RunCommand
from ojarvis_shell.tools.sudo import ProposeAdmin
from ojarvis_shell.tools.tab import tab_tools
from ojarvis_shell.tools.tab_sight import TabSight, sight_tools
from ojarvis_shell.tools.tab_step import TabStep
from ojarvis_shell.tools.terminal import OpenShell
from ojarvis_shell.voice.stt_meta import MetaTranscriber
from ojarvis_shell.voice.tts_meta import MMS_EN_FILES, MMS_FILES, EspeakTTS, MmsVoices

# « Tout Meta » : seules les voix de Meta se téléchargent, et Réglages › Modèles locaux les
# propose à l'import manuel.
META_MODEL_SETS.update(
    {"voix_meta": ("mms-tts-fra", MMS_FILES), "voix_meta_en": ("mms-tts-eng", MMS_EN_FILES)}
)
_MMS_SETS = (
    ModelSet("voix_meta", "Voix de Meta (MMS)", "mms-tts-fra", MMS_FILES),
    ModelSet("voix_meta_en", "Voix anglaise de Meta (MMS)", "mms-tts-eng", MMS_EN_FILES),
)
LOCAL_MODELS[-1:-1] = [m for m in _MMS_SETS if m not in LOCAL_MODELS]  # avant la mémoire


class ShellState:
    """Les objets de l'extension que ses routes retrouvent (`services.added.state`)."""

    def __init__(
        self, chrome: ChromeBridge, marks: ScreenMarks, share: ScreenShare, pointer: PortalPointer
    ) -> None:
        self.chrome = chrome
        self.marks = marks
        self.share = share
        self.pointer = pointer


async def attach(lent: Lent) -> Added:
    """Assemble l'extension avec ce que le moteur lui prête, et inscrit ses outils."""
    desktop = lent.config.paths.state_dir / "desktop"
    shell_dir = lent.config.paths.state_dir / "shell"

    def api_key() -> str | None:
        return lent.secrets.get("MODEL_API_KEY")

    async def share_event(event: str, payload: dict[str, Any]) -> None:
        await lent.bus.publish(event, payload, persist=False)

    # Trouver puis cliquer un élément de l'écran (#48) : SAM 3.1 encadre, Muse Spark choisit,
    # le portail de GNOME clique (avec ton accord).
    marks = ScreenMarks(desktop / "reperes")
    sam = SamClient(api_key, lent.http, lent.tracer)
    # L'écran partagé en direct : les outils lisent sa dernière image au lieu d'une capture.
    share = ScreenShare(desktop / "partage-ecran.jeton", notify=share_event)
    screen = live_or_capture(share, lent.capture)
    locator = Locator(sam, lent.router, marks, screen)
    pointer = PortalPointer(desktop / "controle-a-distance.jeton")
    filmer = Filmer(share, api_key, lent.http, desktop / "travail", tracer=lent.tracer)
    # Ton onglet Chrome : le panneau de l'extension y fait les actions validées.
    chrome = ChromeBridge()
    servers = BackgroundSessions()  # les serveurs lancés par le mode shell
    machine = MachineCard(lent.memory, lent.journal, lent.home)
    floor = RELEVANCE_FLOOR.get(lent.embedder.model_id, 0.5)

    # Regarder l'écran lit aussi le partage en direct, s'il est en cours.
    lent.tools.unregister(LookAtScreen.name)
    tools: list[Tool[Any]] = [
        LookAtScreen(lent.router, lent.screen_mode, screen),
        FindOnScreen(locator, lent.screen_mode),
        ClickOnScreen(marks, pointer, screen),
        BringWindow(lent.router, pointer, lent.screen_mode, screen),
        ClickInApp(lent.router, pointer, locator, pointer, lent.screen_mode, grab=screen),
        FilmScreen(filmer, lent.screen_mode),
        EditFile(),
        # Proposés seulement en mode shell ; journaux et captures dans l'état du moteur.
        RunCommand(lent.home, log_dir=shell_dir, sessions=servers),
        LookAtPage(
            lent.router,
            lent.screen_mode,
            servers,
            capture_dir=shell_dir / "captures",
        ),
        OpenShell(lent.home),  # « passe en mode shell » : ouvre un terminal
        ProposeAdmin(),  # sudo : proposé par Jarvis, lancé par toi
        *tab_tools(chrome),  # proposés seulement dans le panneau de Chrome
        TabStep(chrome, lent.router),  # plusieurs gestes, un seul plan
        *sight_tools(TabSight(chrome, locator)),  # et le secours visuel (SAM 3.1)
        *impasse_tools(lent.memory, floor=floor),
        ReadMachineMap(machine),
    ]
    for tool in tools:
        lent.tools.register(tool)
    capabilities = lent.tools.get("lister_capacites")
    if isinstance(capabilities, ListCapabilities):
        capabilities.hide(lambda item: item["chrome_tab"] and not chrome.connected())
        capabilities.add_section("Extension Chrome", lambda: [_chrome_state(chrome)])
    return Added(
        chrome_connected=chrome.connected,
        reviewer=CodeReviewer(lent.router),
        machine=machine,
        routers=routers(),
        state=ShellState(chrome, marks, share, pointer),
        stop=share.stop,
    )


def _chrome_state(chrome: ChromeBridge) -> str:
    if chrome.connected():
        return "branchée : Jarvis voit tes onglets Chrome et y agit (avec ta validation)."
    return "non branchée : Jarvis ne voit pas ton Chrome (ouvre-le, avec l'extension d'O-jarvis)."


def routers() -> list[APIRouter]:
    return [routes_chrome.router, routes_screen.router]


def talks_to_network(command: str) -> bool:
    return _talks_to_network(command)


def commands() -> dict[str, Callable[[list[str]], int]]:
    """Les commandes du moteur empaqueté : le terminal de Jarvis, l'hôte de l'extension Chrome."""
    from ojarvis_shell.chrome.host import main as chrome_host  # noqa: PLC0415 - à la demande
    from ojarvis_shell.shell.cli import main as shell  # noqa: PLC0415

    return {"shell": shell, "chrome-host": chrome_host}


async def meta_voice_parts(
    models: Path,
    api_key: Callable[[], str | None],
    http: httpx.AsyncClient,
    on_fallback: Callable[[str], None],
) -> dict[str, Any]:
    """« Tout Meta » : Muse Voice Transcribe écoute, la voix MMS de Meta parle (sur le PC),
    espeak-ng en secours. Pas de mot de réveil : le raccourci, le bouton micro ou « /voix »."""
    mms = MmsVoices(models)
    await mms.warm_up()
    return {
        "wake": None,
        "vad": EnergyVad(),
        "stt": MetaTranscriber(api_key, http),
        "tts": FallbackTTS(mms, EspeakTTS(), on_fallback=on_fallback),
    }
