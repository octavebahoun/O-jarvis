"""Le moteur d'O-jarvis simulé, avec O-jarvis Shell branché dessus."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from ojarvis_shell.shell.machine import MachineCard
from tests.shell.machine_fake import fake_machine


@pytest.fixture
def client(client: TestClient) -> TestClient:
    """Le moteur simulé : l'extension y est branchée, et la carte de la machine n'est jamais
    relevée sur la machine qui lance les tests."""
    added = client.app.state.services.added  # type: ignore[attr-defined]
    assert added is not None, "O-jarvis Shell n'est pas branché sur le moteur"
    assert isinstance(added.machine, MachineCard)
    added.machine._collect = fake_machine
    return client
