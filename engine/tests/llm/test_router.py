from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import BaseModel

from ojarvis.db.engine import Database
from ojarvis.db.migrate import upgrade_to_head
from ojarvis.events.bus import EventBus
from ojarvis.llm.catalog import Catalog
from ojarvis.llm.errors import (
    AuthenticationFailedError,
    ContentRefusedError,
    NoRouteAvailableError,
    ProviderUnavailableError,
    StructuredOutputError,
)
from ojarvis.llm.policy import Relay, Route
from ojarvis.llm.providers.fake import FakeProvider, FakeReply
from ojarvis.llm.router import Constraints, ModelRouter, RoutedStream
from ojarvis.llm.structured import TOOL_NAME, generate_structured
from ojarvis.llm.types import (
    ImagePart,
    LLMRequest,
    Message,
    RouteInfo,
    TextDelta,
    ToolCall,
)
from ojarvis.testing.llm import POLICY, Clock, make_router, request
from ojarvis.tracing.tracer import TraceQueries, Tracer


def test_routes_skip_unconfigured_and_respect_sensitivity() -> None:
    router = make_router(deepseek=FakeProvider("deepseek", configured=False))
    assert [r.provider for r in router.routes("chat")] == ["openrouter", "ollama"]
    sensitive = router.routes("chat", Constraints(sensitive=True))
    assert [r.provider for r in sensitive] == ["ollama"]
    assert [r.provider for r in router.routes("chat", Constraints(offline=True))] == ["ollama"]


def test_routes_filter_on_capabilities() -> None:
    router = make_router()
    # DeepSeek n'a pas la vision : la route vision passe au modèle local.
    assert [r.provider for r in router.routes("vision", Constraints(needs_vision=True))] == [
        "ollama"
    ]


def test_override_comes_first() -> None:
    router = make_router(overrides={"chat": "ollama/ministral-3:3b"})
    assert router.routes("chat")[0] == Route("ollama", "ministral-3:3b")
    reasoning = make_router(overrides={"reasoning": "deepseek/deepseek-flash"}).routes("reasoning")[
        0
    ]
    assert reasoning.thinking  # la tâche de réflexion garde sa réflexion


async def test_fallback_before_first_token_publishes_event() -> None:
    bus = EventBus()
    failing = FakeProvider(
        "deepseek", script=lambda r: FakeReply(error=ProviderUnavailableError("panne"))
    )
    router = make_router(deepseek=failing, bus=bus)
    async with bus.stream("llm.*") as queue:
        events = [e async for e in router.stream("chat", request())]
        types = [queue.get_nowait().type for _ in range(queue.qsize())]
    info = events[0]
    assert isinstance(info, RouteInfo)
    assert info.provider == "openrouter"
    assert info.fallback_from == "deepseek/deepseek-flash"
    assert "".join(e.text for e in events if isinstance(e, TextDelta)).startswith("Réponse de test")
    assert types == ["llm.fallback"]


async def test_no_fallback_once_text_was_sent() -> None:
    broken = FakeProvider(
        "deepseek",
        script=lambda r: FakeReply(
            text="début", error=ProviderUnavailableError("coupure"), fail_after_text=True
        ),
    )
    router = make_router(deepseek=broken)
    with pytest.raises(ProviderUnavailableError):
        _ = [e async for e in router.stream("chat", request())]


async def test_refusal_is_not_retried_elsewhere() -> None:
    refusing = FakeProvider(
        "deepseek", script=lambda r: FakeReply(error=ContentRefusedError("non"))
    )
    router = make_router(deepseek=refusing)
    with pytest.raises(ContentRefusedError):
        await router.complete("chat", request())


async def test_circuit_breaker_and_recovery() -> None:
    bus = EventBus()
    clock = Clock()
    state = {"fail": True}

    def script(r: LLMRequest) -> FakeReply:
        return (
            FakeReply(error=ProviderUnavailableError("panne"))
            if state["fail"]
            else FakeReply(text="ok")
        )

    router = make_router(deepseek=FakeProvider("deepseek", script=script), bus=bus, clock=clock)
    async with bus.stream("llm.provider_*") as queue:
        await router.complete("chat", request())  # 1er échec : pas encore de mise à l'écart
        assert router.health["deepseek"].available(clock.now)
        await router.complete("chat", request())  # 2e échec : écarté 30 s
        assert not router.health["deepseek"].available(clock.now)
        deepseek_requests = len(router.providers["deepseek"].requests)  # type: ignore[attr-defined]
        await router.complete(
            "chat", request()
        )  # pendant la mise à l'écart : DeepSeek n'est pas appelé
        assert len(router.providers["deepseek"].requests) == deepseek_requests  # type: ignore[attr-defined]
        clock.now += 31
        state["fail"] = False
        result = await router.complete("chat", request())
        assert result.provider == "deepseek"
        types = [queue.get_nowait().type for _ in range(queue.qsize())]
    assert types == ["llm.provider_degraded", "llm.provider_recovered"]
    status = {s["provider"]: s for s in router.status()}
    assert status["deepseek"]["degraded"] is False


async def test_auth_failure_sidelines_provider_for_ten_minutes() -> None:
    clock = Clock()
    router = make_router(
        deepseek=FakeProvider(
            "deepseek", script=lambda r: FakeReply(error=AuthenticationFailedError("clé"))
        ),
        clock=clock,
    )
    await router.complete("chat", request())
    assert router.health["deepseek"].open_until == clock.now + 600


async def test_every_route_down_raises_last_error() -> None:
    down = lambda r: FakeReply(error=ProviderUnavailableError("panne"))  # noqa: E731
    router = make_router(
        deepseek=FakeProvider("deepseek", script=down),
        openrouter=FakeProvider("openrouter", script=down),
        ollama=FakeProvider("ollama", script=down, local=True),
    )
    with pytest.raises(ProviderUnavailableError):
        await router.complete("chat", request())
    with pytest.raises(NoRouteAvailableError):
        await make_router().complete("title", request())  # aucune politique pour cette tâche ici


async def test_complete_aggregates_and_traces(tmp_path: Path) -> None:
    db_path = tmp_path / "t.db"
    upgrade_to_head(db_path)
    db = Database(db_path)
    bus = EventBus()
    tracer = Tracer(db, bus)
    deepseek = FakeProvider(
        "deepseek",
        script=lambda r: FakeReply(
            text="Bonjour Octave.", tool_calls=[ToolCall("1", "noter", {"x": 1})]
        ),
    )
    router = make_router(deepseek=deepseek, tracer=tracer)
    seen: list[str] = []
    async with tracer.trace("tour", kind="chat") as trace:
        result = await router.complete("chat", request("salut"), on_text=seen.append)
    assert result.text == "Bonjour Octave."
    assert "".join(seen) == "Bonjour Octave."
    assert result.tool_calls[0].name == "noter"
    assert result.provider == "deepseek"
    assert result.cost_usd > 0
    assert result.ttft_ms is not None
    found = await TraceQueries(db).get(trace.id)
    assert found is not None
    span = found[1][0]
    assert span.kind == "llm"
    assert span.attributes["provider"] == "deepseek"
    assert span.attributes["model"] == "deepseek-flash"
    assert span.attributes["tokens_out"] > 0
    assert span.attributes["cost_usd"] > 0
    assert span.attributes["attempts"] == ["deepseek/deepseek-flash"]


async def test_reasoning_route_turns_thinking_on() -> None:
    deepseek = FakeProvider("deepseek")
    router = make_router(deepseek=deepseek)
    await router.complete("reasoning", request())
    sent, model = deepseek.requests[0]
    assert model == "deepseek-v4-pro"
    assert sent.thinking is True


async def test_images_require_vision() -> None:
    ollama = FakeProvider("ollama", local=True)
    router = make_router(ollama=ollama)
    vision = LLMRequest([Message("user", "décris", images=(ImagePart("QUJD"),))])
    result = await router.complete("vision", vision)
    assert result.provider == "ollama"


async def test_a_local_only_request_checks_ollama_again_first() -> None:
    # Ollama a pu démarrer après le moteur : une demande privée le revérifie avant de router.
    ollama = FakeProvider("ollama", local=True, configured=False)
    calls: list[str] = []

    async def refresh() -> None:
        calls.append("refresh")
        ollama._configured = True

    router = ModelRouter(
        {"deepseek": FakeProvider("deepseek"), "ollama": ollama},
        Catalog(),
        policy=POLICY,
        refresh_local=refresh,
    )
    await router.complete("chat", request())
    assert calls == []  # une demande ordinaire ne coûte rien de plus
    result = await router.complete("chat", request(), Constraints(sensitive=True))
    assert (result.provider, calls) == ("ollama", ["refresh"])


# ------------------------------------------------------------------ sorties structurées


class Fait(BaseModel):
    sujet: str
    confiance: float


async def test_structured_from_tool_call() -> None:
    deepseek = FakeProvider(
        "deepseek",
        script=lambda r: FakeReply(
            tool_calls=[ToolCall("1", TOOL_NAME, {"sujet": "café", "confiance": 0.9})]
        ),
    )
    fait = await generate_structured(
        make_router(deepseek=deepseek), "chat", [Message("user", "x")], Fait
    )
    assert fait == Fait(sujet="café", confiance=0.9)
    sent = deepseek.requests[0][0]
    assert sent.tools[0].name == TOOL_NAME
    assert sent.tools[0].parameters["required"] == ["sujet", "confiance"]


async def test_structured_from_noisy_json_text() -> None:
    deepseek = FakeProvider(
        "deepseek", script=lambda r: FakeReply(text='Voici : {"sujet": "thé", "confiance": 0.5}]')
    )
    fait = await generate_structured(
        make_router(deepseek=deepseek), "chat", [Message("user", "x")], Fait
    )
    assert fait.sujet == "thé"


async def test_structured_retries_once_with_the_error() -> None:
    replies = iter(
        [
            FakeReply(text='{"sujet": "eau"}'),  # champ manquant
            FakeReply(text='{"sujet": "eau", "confiance": 1}'),
        ]
    )
    deepseek = FakeProvider("deepseek", script=lambda r: next(replies))
    fait = await generate_structured(
        make_router(deepseek=deepseek), "chat", [Message("user", "x")], Fait
    )
    assert fait.confiance == 1
    second = deepseek.requests[1][0]
    assert "pas exploitable" in second.messages[-1].content


async def test_structured_fails_cleanly_after_two_attempts() -> None:
    deepseek = FakeProvider("deepseek", script=lambda r: FakeReply(text="je ne sais pas"))
    with pytest.raises(StructuredOutputError):
        await generate_structured(
            make_router(deepseek=deepseek), "chat", [Message("user", "x")], Fait
        )
    assert len(deepseek.requests) == 2


async def test_slow_first_token_falls_back_quickly() -> None:
    slow = FakeProvider("deepseek", delay_s=2.0)
    router = ModelRouter(
        {"deepseek": slow, "openrouter": FakeProvider("openrouter")},
        Catalog(),
        policy=POLICY,
        first_event_timeout={"chat": 0.05},
    )
    events = [e async for e in router.stream("chat", request())]
    info = events[0]
    assert isinstance(info, RouteInfo)
    assert info.provider == "openrouter"
    assert router.health["deepseek"].last_error == "FirstTokenTimeoutError"


async def test_secrets_never_reach_a_model() -> None:
    from ojarvis.security.redaction import REDACTOR  # noqa: PLC0415

    REDACTOR.add_value("valeur-du-trousseau-xyz")
    deepseek = FakeProvider("deepseek")
    router = make_router(deepseek=deepseek)
    key = "ghp_" + "a" * 36
    text = f"Ma clé est {key} et l'autre valeur-du-trousseau-xyz, garde-les."
    [e async for e in router.stream("chat", request(text))]
    sent = deepseek.requests[0][0].messages[-1].content
    assert key not in sent
    assert "valeur-du-trousseau-xyz" not in sent
    assert sent.count("***") == 2


async def test_a_relay_goes_first_and_the_journal_says_why(tmp_path: Path) -> None:
    db_path = tmp_path / "t.db"
    upgrade_to_head(db_path)
    db = Database(db_path)
    tracer = Tracer(db, EventBus())
    router = make_router(profile="meta", tracer=tracer)
    relay = Relay(Route("meta", "muse-wonder-1.0", thinking=True), "2 échecs de « npm test »")
    assert router.routes("reasoning", first=relay.route)[0] == relay.route
    async with tracer.trace("tour", kind="chat") as trace:
        events = [e async for e in router.stream_traced("reasoning", request("x"), relay=relay)]
    info = next(e for e in events if isinstance(e, RouteInfo))
    assert (info.provider, info.model) == ("meta", "muse-wonder-1.0")
    found = await TraceQueries(db).get(trace.id)
    assert found is not None
    assert found[1][0].attributes["relais"] == "2 échecs de « npm test »"


async def test_each_call_of_a_turn_shows_only_its_own_attempts(tmp_path: Path) -> None:
    # Un tour avec des outils fait deux appels : le second ne doit pas compter le premier.
    db_path = tmp_path / "t.db"
    upgrade_to_head(db_path)
    db = Database(db_path)
    tracer = Tracer(db, EventBus())
    router = make_router(tracer=tracer)
    state = RoutedStream()
    async with tracer.trace("tour", kind="chat") as trace:
        for _ in range(2):
            async for _event in router.stream_traced("chat", request("x"), state=state):
                pass
    found = await TraceQueries(db).get(trace.id)
    assert found is not None
    assert [span.attributes["attempts"] for span in found[1]] == [
        ["deepseek/deepseek-flash"],
        ["deepseek/deepseek-flash"],
    ]
