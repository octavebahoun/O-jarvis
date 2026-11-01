from __future__ import annotations

from pathlib import Path

import pytest

from ojarvis.testing.fixtures import (  # noqa: F401 - les fixtures du moteur simulé
    DEV_ORIGIN,
    TOKEN,
    Router,
    _reset_redactor,
    backend,
    client,
    config,
    home,
    llm,
    services,
    upstream,
)

# Un faux menu d'applications (fichiers .desktop), pour le contrôle du PC.
ENTRY = """[Desktop Entry]
Type=Application
Name={name}
{extra}
Exec={exec}
"""


def write_app(folder: Path, app_id: str, name: str, exec_: str, extra: str = "") -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{app_id}.desktop").write_text(
        ENTRY.format(name=name, exec=exec_, extra=extra), encoding="utf-8"
    )


@pytest.fixture
def menu(tmp_path: Path) -> Path:
    folder = tmp_path / "applications"
    write_app(
        folder,
        "org.gnome.Calculator",
        "Calculator",
        "gnome-calculator %U",
        "Name[fr]=Calculatrice\nKeywords=calcul;maths;",
    )
    write_app(folder, "firefox", "Firefox", "env MOZ_ENABLE_WAYLAND=1 /usr/lib/firefox/firefox %u")
    write_app(folder, "cache", "Caché", "cache", "NoDisplay=true")
    write_app(folder, "kde-only", "KDE seulement", "kde", "OnlyShowIn=KDE;")
    write_app(
        folder,
        "org.gnome.TextEditor",
        "Text Editor",
        "gnome-text-editor",
        "Name[fr]=Éditeur de texte",
    )
    return folder
