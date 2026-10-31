"""Ce que partagent les tests du mode shell : le dossier de projet de l'utilisateur."""

from __future__ import annotations

from pathlib import Path

import pytest

from ojarvis.services import Services


@pytest.fixture
def project(services: Services) -> Path:
    assert services.config.user_home is not None
    folder = services.config.user_home / "projet"
    folder.mkdir(parents=True)
    return folder
