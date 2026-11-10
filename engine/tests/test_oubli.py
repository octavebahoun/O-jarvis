"""« Oublier une information ? » : effacer ce que l'utilisateur demande d'oublier ; tout effacer
seulement après son « oui »."""

from __future__ import annotations

from typing import Any

import numpy as np
from fastapi.testclient import TestClient

from ojarvis.llm.providers.fake import FakeProvider
from ojarvis.memory.embeddings import Kind, Vectors
from ojarvis.services import Services
from ojarvis.systeme1 import oubli
from ojarvis.systeme1.decideurs import Choix, Decideurs, Systeme1
from tests.test_doutes import Scenario, parler

INDICES = ("Oublie que", "Oublie tout", "Oublie ça")


def choix(garder: float, supprimer: float, demander: float) -> dict[str, Choix]:
    probas = {"garder": garder, "supprimer": supprimer, "demander": demander}
    meilleure = max(probas, key=lambda r: probas[r])
    return {"oublier": Choix(meilleure, probas[meilleure], probas)}


def test_a_request_to_forget_is_anything_unlikely_to_keep_and_only_yes_wipes() -> None:
    assert oubli.demande_oubli(choix(0.05, 0.15, 0.80))  # « oublie tout » vu comme flou
    assert oubli.demande_oubli(choix(0.34, 0.24, 0.42))  # « oublie que je bosse chez Orange »
    assert not oubli.demande_oubli(choix(0.89, 0.05, 0.06))  # « j'ai oublié mon parapluie »
    assert not oubli.demande_oubli(None)
    assert oubli.confirme("Oui, vas-y")
    assert oubli.confirme("ok")
    assert not oubli.confirme("non, finalement garde tout")
    assert not oubli.confirme("je sais pas, oui peut-être")


class Indices:
    model_id = "indices-oubli"
    dim = len(INDICES)
    status = "prêt"

    async def embed(self, texts: list[str], kind: Kind) -> Vectors:
        return np.array([[float(i in t) for i in INDICES] for t in texts], np.float32)


def decideurs() -> Decideurs:
    # Lignes : « Oublie que », « Oublie tout », « Oublie ça », biais.
    apprendre = np.array([[8, 0], [8, 0], [8, 0], [0, 2]], np.float32)  # rien, fait
    sensible = np.array([[0, 0], [0, 0], [0, 0], [2, 0]], np.float32)  # ordinaire, secret
    oublier = np.array(  # demander, garder, supprimer
        [[0, 0, 8], [0, 0, 8], [8, 0, 0], [0, 2, 0]], np.float32
    )
    return Decideurs(
        "indices-oubli",
        {"apprendre": apprendre, "sensible": sensible, "oublier": oublier},
        {
            "apprendre": ["rien", "fait"],
            "sensible": ["ordinaire", "secret"],
            "oublier": ["demander", "garder", "supprimer"],
        },
    )


def brancher(client: TestClient, services: Services) -> dict[str, int]:
    systeme1 = Systeme1(Indices(), decideurs())
    services.chat._systeme1 = systeme1
    services.context.systeme1 = systeme1
    ids = {}
    for kind, texte in (
        ("fait", "L'utilisateur habite à Lyon."),
        ("preference", "L'utilisateur aime le jazz."),
        ("profil", "L'utilisateur s'appelle Octave."),
        ("machine", "Le PC a 8 Go de mémoire vive."),
    ):
        ids[texte] = client.post("/v1/memory", json={"kind": kind, "content": texte}).json()["id"]
    client.post("/v1/memory/graph/entities", json={"name": "Lyon", "kind": "lieu"})
    return ids


def restants(client: TestClient) -> set[str]:
    return {m["content"] for m in client.get("/v1/memory").json()}


def rien(**champs: Any) -> dict[str, Any]:
    return {"faits": [], **champs}


def test_a_clear_request_forgets_what_the_model_names(
    client: TestClient, services: Services, llm: dict[str, FakeProvider]
) -> None:
    ids = brancher(client, services)
    lyon = ids["L'utilisateur habite à Lyon."]
    scenario = Scenario(
        ["C'est oublié : je ne sais plus où tu habites.", "Dommage !"],
        # 9999 n'a pas été montré au modèle : jamais effacé.
        [rien(oublie=[lyon, 9999]), rien(oublie=[ids["L'utilisateur aime le jazz."]])],
    )
    llm["deepseek"]._script = scenario
    parler(client, services, "Oublie que j'habite à Lyon", None)
    assert "ce que tu oublies" in scenario.systemes[0]
    assert "mets dans « oublie »" in scenario.consignes[0]
    assert "L'utilisateur habite à Lyon." not in restants(client)
    assert "L'utilisateur aime le jazz." in restants(client)

    # Pas une demande d'oubli : ce que le modèle désigne reste.
    parler(client, services, "J'ai oublié mes clés chez le jazzman", None)
    assert "L'utilisateur aime le jazz." in restants(client)


def test_everything_goes_only_after_a_yes(
    client: TestClient, services: Services, llm: dict[str, FakeProvider]
) -> None:
    brancher(client, services)
    scenario = Scenario(
        ["Tu es sûr ? Tout ce que je sais de toi sera effacé.", "C'est fait."],
        [rien(oublie_tout=True)],
    )
    llm["deepseek"]._script = scenario
    conversation = parler(client, services, "Oublie tout ce que tu sais sur moi", None)
    assert "demande-lui de confirmer" in scenario.systemes[0]
    assert len(restants(client)) == 4  # rien avant le « oui »

    parler(client, services, "Oui, vas-y", conversation)
    assert "L'utilisateur confirme" in scenario.systemes[1]
    assert restants(client) == {"Le PC a 8 Go de mémoire vive."}  # la machine n'est pas lui
    assert client.get("/v1/memory/graph").json()["entities"] == []
    assert not services.context.confirmations


def test_anything_but_yes_cancels(
    client: TestClient, services: Services, llm: dict[str, FakeProvider]
) -> None:
    brancher(client, services)
    scenario = Scenario(["Tu es sûr ?", "D'accord, je garde tout."], [rien(oublie_tout=True)])
    llm["deepseek"]._script = scenario
    conversation = parler(client, services, "Oublie tout ce que tu sais sur moi", None)
    parler(client, services, "Non, finalement garde tout", conversation)
    assert "n'a pas confirmé" in scenario.systemes[1]
    assert len(restants(client)) == 4
    assert not services.context.confirmations


def test_a_vague_request_makes_jarvis_ask_what(
    client: TestClient, services: Services, llm: dict[str, FakeProvider]
) -> None:
    brancher(client, services)
    scenario = Scenario(["Oublier quoi, exactement ?"], [])
    llm["deepseek"]._script = scenario
    parler(client, services, "Oublie ça", None)
    assert "demande-lui quoi si ce n'est pas clair" in scenario.systemes[0]
    assert len(restants(client)) == 4
