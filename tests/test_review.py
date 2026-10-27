"""Le relecteur du mode shell : son avis, lu dans une réponse en texte simple."""

from __future__ import annotations

import pytest

from ojarvis.agent.review import CodeReviewer, Review, parse_review
from ojarvis.llm.catalog import Catalog
from ojarvis.llm.errors import ProviderUnavailableError
from ojarvis.llm.providers.fake import FakeProvider, FakeReply
from ojarvis.llm.router import META_POLICY, ModelRouter, Route
from ojarvis.llm.types import LLMRequest


@pytest.mark.parametrize(
    ("text", "ok", "notes"),
    [
        ("OK\nRien à redire.", True, "Rien à redire."),
        ("**OK** — le correctif est juste.", True, "le correctif est juste."),
        ("PROBLÈME\nLigne 3 : division par zéro si la liste est vide.", False, "Ligne 3 :"),
        ("Probleme : il manque l'import de os.", False, "il manque l'import de os."),
        ("Je ne suis pas sûr de comprendre.", None, "Je ne suis pas sûr"),
        ("", None, ""),
    ],
)
def test_the_verdict_is_read_from_the_first_line(text: str, ok: bool | None, notes: str) -> None:
    verdict, read = parse_review(text)
    assert verdict is ok
    assert read.startswith(notes)


def test_the_author_reads_the_verdict_and_who_gave_it() -> None:
    review = Review(False, "Ligne 3 : division par zéro.", "meta", "muse-wonder-1.0")
    assert review.for_author() == (
        "Relecture (meta / muse-wonder-1.0) : problème. Ligne 3 : division par zéro."
    )


def router_with(provider: FakeProvider) -> ModelRouter:
    return ModelRouter({"deepseek": provider}, Catalog())


async def test_the_reviewer_reads_the_diff_as_data() -> None:
    seen: list[LLMRequest] = []

    def script(request: LLMRequest) -> FakeReply:
        seen.append(request)
        return FakeReply(text="PROBLÈME\nIl manque le cas de la liste vide.")

    reviewer = CodeReviewer(router_with(FakeProvider("deepseek", script=script)))
    review = await reviewer.review("Corrige la moyenne", ["--- a/x.py\n+++ b/x.py\n+ok"])
    assert review is not None
    assert review.ok is False
    assert review.notes == "Il manque le cas de la liste vide."
    assert review.provider == "deepseek"
    sent = seen[0].messages[-1].content
    assert "Corrige la moyenne" in sent
    assert '<donnees origine="diff">' in sent  # le diff est une donnée, jamais une consigne


async def test_a_reviewer_that_fails_does_not_block_the_plan() -> None:
    def down(_request: LLMRequest) -> FakeReply:
        return FakeReply(error=ProviderUnavailableError("panne"))

    reviewer = CodeReviewer(router_with(FakeProvider("deepseek", script=down)))
    assert await reviewer.review("x", ["diff"]) is None


def test_with_the_meta_profile_muse_wonder_reviews() -> None:
    assert META_POLICY["review"][0] == Route("meta", "muse-wonder-1.0", thinking=True)
