"""« Mettre à jour un souvenir ? » et « Information incertaine ? » : Jarvis pose la question
dans sa réponse, et la réponse de l'utilisateur tranche."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import numpy as np
from fastapi.testclient import TestClient

from ojarvis.llm.providers.fake import FakeProvider, FakeReply
from ojarvis.llm.structured import TOOL_NAME
from ojarvis.llm.types import LLMRequest, ToolCall
from ojarvis.memory.embeddings import Kind, Vectors
from ojarvis.memory.store import MemoryHit, MemoryRecord
from ojarvis.services import Services
from ojarvis.systeme1.decideurs import Decideurs, Systeme1
from ojarvis.systeme1.doutes import Doute, plus_proche
from ojarvis.testing.chat import read_sse

INDICES = ("embauché", "quitté", "crois", "oui")


class Indices:
    """Un axe par indice : un nouvel emploi (ambigu), un départ, un doute, un « oui »."""

    model_id = "indices"
    dim = len(INDICES)
    status = "prêt"

    async def embed(self, texts: list[str], kind: Kind) -> Vectors:
        return np.array([[float(i in t) for i in INDICES] for t in texts], np.float32)


def decideurs() -> Decideurs:
    def lignes(par_indice: dict[int, list[float]], biais: list[float], n: int) -> Any:
        poids = np.zeros((n + 1, len(biais)), np.float32)
        for indice, valeurs in par_indice.items():
            poids[indice] = valeurs
        poids[-1] = biais
        return poids

    d = len(INDICES)
    return Decideurs(
        "indices",
        {
            "apprendre": lignes({3: [8, 0]}, [0, 2], d),  # rien, fait
            "sensible": lignes({}, [2, 0], d),  # ordinaire, secret
            "incertain": lignes({2: [0, 8, 0]}, [2, 0, 0], d),  # accepter, demander, verifier
            # Paire [souvenir, message, produit, écart] : seul le message compte ici.
            "mise_a_jour": lignes(
                {d + 0: [0, 8, 0, 0], d + 1: [0, 0, 0, 8]}, [0, 0, 2, 0], 4 * d
            ),  # ajouter, demander, garder_les_deux, remplacer
        },
        {
            "apprendre": ["rien", "fait"],
            "sensible": ["ordinaire", "secret"],
            "incertain": ["accepter", "demander", "verifier"],
            "mise_a_jour": ["ajouter", "demander", "garder_les_deux", "remplacer"],
        },
    )


def souvenir(item_id: int, texte: str, kind: str = "fait") -> MemoryRecord:
    now = datetime.now(UTC)
    return MemoryRecord(item_id, kind, texte, "manuel", 1.0, False, now, now, None, 0)


def test_only_a_memory_really_about_the_message_is_compared() -> None:
    orange = souvenir(1, "L'utilisateur travaille chez Orange.")
    machine = souvenir(2, "Le PC a 8 Go de RAM.", kind="machine")
    assert plus_proche([MemoryHit(orange, 0.5, "sens")], 0.69) is None
    assert plus_proche([MemoryHit(orange, 0.67, "mots")], 0.6) is None
    hits = [MemoryHit(machine, 0.9, "sens"), MemoryHit(orange, 0.8, "les deux")]
    assert plus_proche(hits, 0.69) == orange
    assert Doute(orange, "demander").protege == 1
    assert Doute(orange, "remplacer").protege is None
    assert Doute(orange, "remplacer").remplace == 1
    assert Doute(incertain="demander").protege is None


class Scenario:
    """Le faux modèle : il répond, titre, et extrait ce qu'on lui dit, tour après tour."""

    def __init__(self, reponses: list[str], extractions: list[dict[str, Any]]) -> None:
        self._reponses: Iterator[str] = iter(reponses)
        self._extractions: Iterator[dict[str, Any]] = iter(extractions)
        self.systemes: list[str] = []
        self.consignes: list[str] = []

    def __call__(self, request: LLMRequest) -> FakeReply:
        if request.tools and request.tools[0].name == TOOL_NAME:
            self.consignes.extend(m.content for m in request.messages if "Échange :" in m.content)
            return FakeReply(tool_calls=[ToolCall("m", TOOL_NAME, next(self._extractions))])
        if "titre" in request.messages[0].content:
            return FakeReply(text="Travail")
        self.systemes.append(request.messages[0].content)
        return FakeReply(text=next(self._reponses))


def parler(client: TestClient, services: Services, texte: str, conversation: str | None) -> str:
    corps: dict[str, str] = {"text": texte}
    if conversation:
        corps["conversation_id"] = conversation
    events = read_sse(client.post("/v1/chat", json=corps).text)
    assert client.portal is not None
    client.portal.call(services.chat.drain)
    return str(events[0][1]["conversation_id"])


def brancher(services: Services) -> None:
    systeme1 = Systeme1(Indices(), decideurs())
    services.chat._systeme1 = systeme1
    services.context.systeme1 = systeme1


def fait(contenu: str, remplace: int | None = None, confiance: float = 0.9) -> dict[str, Any]:
    return {"type": "fait", "contenu": contenu, "confiance": confiance, "remplace": remplace}


def souvenirs(client: TestClient) -> dict[int, str]:
    return {m["id"]: m["content"] for m in client.get("/v1/memory").json()}


def test_jarvis_asks_in_its_reply_and_the_answer_settles_it(
    client: TestClient, services: Services, llm: dict[str, FakeProvider]
) -> None:
    brancher(services)
    orange = client.post(
        "/v1/memory", json={"kind": "fait", "content": "L'utilisateur travaille chez Orange."}
    ).json()["id"]
    scenario = Scenario(
        ["Bravo ! Tu as quitté Orange ?", "C'est noté."],
        [
            # Le modèle croit à un remplacement : le Système 1 garde Orange, la question attend.
            {"faits": [fait("L'utilisateur travaille chez Free.", remplace=orange)]},
            {"faits": [fait("L'utilisateur ne travaille plus chez Orange.", remplace=orange)]},
        ],
    )
    llm["deepseek"]._script = scenario

    conversation = parler(client, services, "Je travaille chez Free, embauché hier", None)
    assert "demande-le-lui" in scenario.systemes[0]
    assert "« L'utilisateur travaille chez Orange. »" in scenario.systemes[0]
    assert "reste vrai : ne le remplace pas" in scenario.consignes[0]
    assert set(souvenirs(client).values()) == {
        "L'utilisateur travaille chez Orange.",
        "L'utilisateur travaille chez Free.",
    }

    # « oui » seul n'apprend rien d'habitude ; ici, il répond à la question.
    parler(client, services, "oui", conversation)
    assert "demande-le-lui" not in scenario.systemes[1]  # pas deux fois la même question
    assert "Jarvis venait de demander" in scenario.consignes[1]
    assert "« Bravo ! Tu as quitté Orange ? »" in scenario.consignes[1]
    apres = souvenirs(client)
    assert apres[orange] == "L'utilisateur ne travaille plus chez Orange."
    assert "L'utilisateur travaille chez Free." in apres.values()
    assert not services.context.attentes


def test_a_clear_change_replaces_the_memory_even_if_the_model_forgets(
    client: TestClient, services: Services, llm: dict[str, FakeProvider]
) -> None:
    brancher(services)
    orange = client.post(
        "/v1/memory", json={"kind": "fait", "content": "L'utilisateur travaille chez Orange."}
    ).json()["id"]
    scenario = Scenario(
        ["Bonne chance chez Free !"],
        [{"faits": [fait("L'utilisateur travaille chez Free.")]}],
    )
    llm["deepseek"]._script = scenario
    parler(client, services, "J'ai quitté Orange, je travaille chez Free", None)
    assert "demande-le-lui" not in scenario.systemes[0]
    assert f"rend faux le souvenir id {orange}" in scenario.consignes[0]
    assert souvenirs(client) == {orange: "L'utilisateur travaille chez Free."}


def test_a_guess_is_not_kept_and_jarvis_asks_to_be_sure(
    client: TestClient, services: Services, llm: dict[str, FakeProvider]
) -> None:
    brancher(services)
    scenario = Scenario(
        ["As-tu fait un test ?"],
        [{"faits": [fait("L'utilisateur est allergique au gluten.", confiance=0.8)]}],
    )
    llm["deepseek"]._script = scenario
    parler(client, services, "Je crois que je suis allergique au gluten", None)
    assert "n'est pas sûr de ce qu'il dit de lui" in scenario.systemes[0]
    assert "Ce message est incertain" in scenario.consignes[0]
    assert souvenirs(client) == {}
