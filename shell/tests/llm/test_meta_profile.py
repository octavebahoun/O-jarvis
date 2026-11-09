"""Le profil « Tout Meta » : seulement des modèles de Meta, secours compris."""

from __future__ import annotations

import pytest
from ojarvis.llm.errors import NoMetaModelError, ProviderUnavailableError
from ojarvis.llm.policy import Route
from ojarvis.llm.providers.fake import FakeProvider, FakeReply
from ojarvis.llm.router import Constraints
from ojarvis.llm.types import LLMRequest, Message, RouteInfo
from ojarvis.testing.llm import make_router

import ojarvis_shell.llm.meta_profile  # noqa: F401 - le profil « Tout Meta » s'inscrit


def test_meta_profile_only_ever_routes_to_meta_models() -> None:
    router = make_router(profile="meta")
    assert [r.key for r in router.routes("chat")] == [
        "meta/muse-spark-1.3",
        "baseten/meta-models/muse-spark-open-1026",
    ]
    assert router.routes("reasoning")[0] == Route("meta", "muse-spark-1.3", thinking=True)
    # Muse Spark voit les images : il lit aussi l'écran.
    assert router.routes("vision", Constraints(needs_vision=True))[0].provider == "meta"
    # Sans le profil, Meta n'apparaît nulle part.
    assert not {"meta", "baseten"} & {r.provider for r in make_router().routes("chat")}


async def test_muse_spark_open_takes_over_when_muse_spark_does_not_answer() -> None:
    down = FakeProvider("meta", script=lambda r: FakeReply(error=ProviderUnavailableError("panne")))
    router = make_router(profile="meta", meta=down)
    request = LLMRequest(messages=[Message("user", "Bonjour")])
    info = next(
        e for e in [e async for e in router.stream("chat", request)] if isinstance(e, RouteInfo)
    )
    assert (info.provider, info.model) == ("baseten", "meta-models/muse-spark-open-1026")
    assert info.fallback_from == "meta/muse-spark-1.3"


async def test_meta_profile_never_falls_back_outside_meta() -> None:
    no_key = make_router(profile="meta", meta=FakeProvider("meta", configured=False))
    assert [r.key for r in no_key.routes("chat")] == ["baseten/meta-models/muse-spark-open-1026"]
    no_keys = make_router(
        profile="meta",
        meta=FakeProvider("meta", configured=False),
        baseten=FakeProvider("baseten", configured=False),
    )
    assert no_keys.routes("chat") == []  # pas de DeepSeek en secours
    with pytest.raises(NoMetaModelError, match="aucun modèle Meta"):
        _ = [e async for e in no_keys.stream("chat", LLMRequest(messages=[Message("user", "?")]))]
    router = make_router(profile="meta")
    # Aucun modèle local n'est de Meta : une demande « sur ce PC » n'a pas de route.
    assert router.routes("chat", Constraints(sensitive=True)) == []
    # Un modèle imposé à la main ne passe que s'il est de Meta.
    forced = make_router(profile="meta", overrides={"chat": "ollama/ministral-3:3b"})
    assert [r.provider for r in forced.routes("chat")] == ["meta", "baseten"]
    wonder = make_router(profile="meta", overrides={"chat": "meta/muse-wonder-1.0"})
    assert wonder.routes("chat")[0] == Route("meta", "muse-wonder-1.0")


async def test_meta_profile_says_so_instead_of_calling_another_model() -> None:
    down = FakeProvider("meta", script=lambda r: FakeReply(error=ProviderUnavailableError("panne")))
    deepseek = FakeProvider("deepseek")
    router = make_router(
        profile="meta",
        meta=down,
        baseten=FakeProvider("baseten", configured=False),
        deepseek=deepseek,
    )
    request = LLMRequest(messages=[Message("user", "Bonjour")])
    with pytest.raises(ProviderUnavailableError):
        _ = [e async for e in router.stream("chat", request)]
    assert deepseek.requests == []
