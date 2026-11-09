"""Une carte de machine de test : jamais relevée sur la machine qui lance les tests."""

from __future__ import annotations

from pathlib import Path

from ojarvis_shell.shell.machine import MachineMap


def fake_machine(home: Path) -> MachineMap:
    del home
    return MachineMap(
        hardware="Matériel : processeur de test (4 fils), mémoire 7,2 Go, échange 2 Go.",
        system="Système : Ubuntu 26.04 LTS, noyau 7.0.0, bureau GNOME (wayland).",
        disks="Disques : / (ext4) 457 Go, 392 Go utilisés (91 %), 43 Go libres.",
        tools="Outils : docker 28.1.1, node 22.11.0, python3 3.14.0, git 2.51.0.",
        services="Services : 40 en marche, dont NetworkManager, snapd ; arrêtés : docker.",
        projects="Projets (dossiers git, les plus récents d'abord) : ~/projet.",
    )
