"""« Résumer la conversation ? » : refait au changement de sujet ou quand la conversation
s'allonge, et envoyé au modèle avec les messages récents."""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
from fastapi.testclient import TestClient

from ojarvis.conversations.resume import AU_MOINS, GARDES, a_resumer, resumer
from ojarvis.conversations.store import MessageRecord
from ojarvis.llm.providers.fake import FakeProvider, FakeReply
from ojarvis.llm.types import LLMRequest
from ojarvis.memory.embeddings import Kind, Vectors
from ojarvis.services import Services
from ojarvis.systeme1.decideurs import Decideurs, Systeme1
from tests.test_doutes import parler

RESUME = "- Le bug CORS est réglé : header Access-Control-Allow-Origin ajouté."


def message(item_id: int, texte: str, status: str = "ok") -> MessageRecord:
    role = "user" if item_id % 2 else "assistant"
    return MessageRecord(item_id, "c", role, texte, status, datetime.now(UTC))


def test_what_the_summary_covers() -> None:
    courts = [message(i, "Un message court.") for i in range(1, 9)]
    assert a_resumer(courts[:4], 0, 99, sujet_change=True) == []  # trop peu pour résumer
    assert a_resumer(courts, 0, 99, sujet_change=False) == []  # ni nouveau sujet ni longueur
    assert a_resumer(courts, 0, 8, sujet_change=True) == courts[:7]  # pas le message du jour
    assert len(a_resumer(courts, 2, 99, sujet_change=True)) == len(courts) - 2  # déjà résumés
    coupe = [*courts[:AU_MOINS], message(9, "Coupé.", status="error")]
    assert coupe[-1] not in a_resumer(coupe, 0, 99, sujet_change=True)
    longs = [message(i, "mot " * 1000) for i in range(1, 11)]
    assert a_resumer(longs, 0, 99, sujet_change=False) == longs[:-GARDES]


class Sujets:
    model_id = "sujets"
    dim = 1
    status = "prêt"

    async def embed(self, texts: list[str], kind: Kind) -> Vectors:
        return np.array([[float("parlons" in t)] for t in texts], np.float32)


def decideurs() -> Decideurs:
    return Decideurs(
        "sujets",
        {
            "apprendre": np.array([[0, 0], [4, 0]], np.float32),  # toujours « rien »
            "resumer": np.array([[0, 8], [2, 0]], np.float32),  # « parlons » : oui
        },
        {"apprendre": ["rien", "fait"], "resumer": ["non", "oui"]},
    )


def test_a_new_topic_summarizes_what_came_before(
    client: TestClient, services: Services, llm: dict[str, FakeProvider]
) -> None:
    services.chat._systeme1 = services.context.systeme1 = Systeme1(Sujets(), decideurs())
    systemes: list[str] = []
    resumes: list[str] = []

    def script(request: LLMRequest) -> FakeReply:
        systeme = request.messages[0].content
        if systeme.startswith("Tu résumes une conversation"):
            resumes.append(request.messages[1].content)
            return FakeReply(text=RESUME)
        if "titre" in systeme:
            return FakeReply(text="Bug CORS")
        systemes.append(systeme)
        return FakeReply(text="D'accord.")

    llm["deepseek"]._script = script
    conversation = None
    for texte in ("Le bug CORS revient", "J'ai ajouté le header", "Ça marche !"):
        conversation = parler(client, services, texte, conversation)
    assert not resumes  # même sujet : rien à résumer
    parler(client, services, "Bon, maintenant parlons du design", conversation)
    assert len(resumes) == 1
    assert "Utilisateur : Le bug CORS revient" in resumes[0]
    assert "maintenant parlons du design" not in resumes[0]
    assert client.portal is not None
    assert conversation is not None
    resume = client.portal.call(services.chat._conversations.summary, conversation)
    assert resume is not None
    assert resume[0] == RESUME

    parler(client, services, "Quelles couleurs pour le logo ?", conversation)
    assert "Résumé de cette conversation jusqu'ici" in systemes[-1]
    assert RESUME in systemes[-1]


def test_a_long_conversation_is_summarized_even_on_the_same_topic(
    client: TestClient, services: Services, llm: dict[str, FakeProvider]
) -> None:
    conversations = services.chat._conversations
    assert client.portal is not None
    portal = client.portal
    llm["deepseek"]._script = lambda request: FakeReply(text="- Une longue revue de code.")
    conversation = portal.call(conversations.create, "app")
    ids = [
        portal.call(conversations.add_message, conversation.id, role, "ligne de code " * 400)
        for role in ("user", "assistant") * 5
    ]
    dernier = ids[-1].id + 1
    fait = portal.call(
        lambda: resumer(
            conversations, services.chat._router, conversation.id, dernier, sujet_change=False
        )
    )
    assert fait
    resume = portal.call(conversations.summary, conversation.id)
    assert resume == ("- Une longue revue de code.", ids[-1 - GARDES].id)
