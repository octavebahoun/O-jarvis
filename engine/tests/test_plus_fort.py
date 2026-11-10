"""« Proposer un modèle plus fort ? » : pour une tâche lourde, l'app propose de refaire avec un
modèle plus fort, s'il y en a un de disponible. Jamais de changement tout seul."""

from __future__ import annotations

import time

import numpy as np
from fastapi.testclient import TestClient

from ojarvis.llm.catalog import Catalog
from ojarvis.llm.policy import Route
from ojarvis.llm.providers.fake import FakeProvider, FakeReply
from ojarvis.llm.router import ModelRouter
from ojarvis.memory.embeddings import Kind, Vectors
from ojarvis.services import Services
from ojarvis.systeme1.decideurs import Choix, Decideurs, Systeme1
from ojarvis.systeme1.plus_fort import plus_fort_disponible, proposer
from ojarvis.testing.chat import read_sse


def sans_relais() -> None:
    return None


def test_the_code_checks_a_stronger_model_is_there() -> None:
    providers = {"deepseek": FakeProvider("deepseek"), "anthropic": FakeProvider("anthropic")}
    router = ModelRouter(providers, Catalog())
    fort = plus_fort_disponible(router, sans_relais)
    assert fort is not None
    assert fort.model == "deepseek/deepseek-v4-pro"  # le premier modèle de raisonnement
    # En « Tout Meta », le modèle de relais passe d'abord.
    relais = Route("anthropic", "claude-opus-5-5")
    par_relais = plus_fort_disponible(router, lambda: (relais, 2))
    assert par_relais is not None
    assert par_relais.model == "anthropic/claude-opus-5-5"
    # Hors service un moment : rien à proposer.
    router.health["deepseek"].open_until = time.monotonic() + 60
    assert plus_fort_disponible(router, sans_relais) is None


def test_nothing_to_offer_when_the_chat_already_uses_the_strongest() -> None:
    router = ModelRouter(
        {"deepseek": FakeProvider("deepseek")},
        Catalog(),
        overrides=lambda: {"chat": "deepseek/deepseek-v4-pro"},
    )
    assert plus_fort_disponible(router, sans_relais) is None


def test_only_a_heavy_task_is_worth_the_offer() -> None:
    assert proposer({"modele_fort": Choix("proposer", 0.8)})
    assert not proposer({"modele_fort": Choix("proposer", 0.45)})
    assert not proposer({"modele_fort": Choix("non", 0.9)})
    assert not proposer(None)


class Lourd:
    model_id = "lourd"
    dim = 1
    status = "prêt"

    async def embed(self, texts: list[str], kind: Kind) -> Vectors:
        return np.array([[float("architecture" in t)] for t in texts], np.float32)


def test_the_app_is_offered_a_stronger_model_for_a_heavy_task(
    client: TestClient, services: Services, llm: dict[str, FakeProvider]
) -> None:
    decideurs = Decideurs(
        "lourd",
        {"modele_fort": np.array([[0, 8], [2, 0]], np.float32)},
        {"modele_fort": ["non", "proposer"]},
    )
    services.context.systeme1 = Systeme1(Lourd(), decideurs)
    llm["deepseek"]._script = lambda request: FakeReply(text="Voici un plan.")

    def contexte(texte: str) -> dict[str, object]:
        events = read_sse(client.post("/v1/chat", json={"text": texte}).text)
        return next(data for kind, data in events if kind == "context")

    lourd = contexte("Conçois l'architecture d'un système de paiement multi-régions")
    assert lourd["stronger"] == {
        "model": "deepseek/deepseek-v4-pro",
        "label": Catalog().get("deepseek", "deepseek-v4-pro").label,  # type: ignore[union-attr]
    }
    assert "stronger" not in contexte("Quelle heure est-il ?")
