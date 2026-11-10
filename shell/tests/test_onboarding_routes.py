"""Les deux routes de l'accueil de l'app : l'état de l'extension Chrome, et l'accord de GNOME
demandé d'avance pour le contrôle du pointeur et du clavier."""

from __future__ import annotations

from collections.abc import Sequence

import pytest
from fastapi.testclient import TestClient
from ojarvis.services import Services

from ojarvis_shell.desktop.pointer import KeyStep


def test_the_app_sees_whether_the_chrome_extension_is_connected(client: TestClient) -> None:
    assert client.get("/v1/chrome/status").json() == {"connected": False}
    client.get("/v1/chrome/actions/next", params={"wait": 0})  # l'extension appelle le moteur
    assert client.get("/v1/chrome/status").json() == {"connected": True}


def test_gnome_is_asked_once_ahead_without_any_gesture(
    client: TestClient, services: Services, monkeypatch: pytest.MonkeyPatch
) -> None:
    sessions: list[list[KeyStep]] = []

    async def keys(steps: Sequence[KeyStep]) -> None:
        sessions.append(list(steps))

    assert services.added is not None
    monkeypatch.setattr(services.added.state.pointer, "keys", keys)
    response = client.post("/v1/desktop/pointer/authorize")
    assert response.json() == {"authorized": True}
    assert sessions == [[]]  # une session ouverte puis refermée, aucune touche
