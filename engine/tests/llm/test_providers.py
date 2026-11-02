"""Tests de contrat des adaptateurs : ce qu'ils envoient au SDK, ce qu'ils renvoient."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import anthropic
import openai
import pytest

from ojarvis.llm.errors import (
    AuthenticationFailedError,
    ContentRefusedError,
    InvalidRequestError,
    NotConfiguredError,
    ProviderUnavailableError,
    QuotaExhaustedError,
    RateLimitedError,
    error_for_status,
)
from ojarvis.llm.providers.anthropic_provider import (
    FALLBACK_BETA,
    AnthropicProvider,
    thinking_config,
    to_anthropic_messages,
)
from ojarvis.llm.providers.anthropic_provider import classify_error as classify_anthropic
from ojarvis.llm.providers.fake import FakeProvider, FakeReply
from ojarvis.llm.providers.openai_compat import (
    DEEPSEEK,
    META,
    OLLAMA,
    OPENAI,
    OPENROUTER,
    OpenAICompatibleProvider,
    classify_error,
    to_openai_message,
)
from ojarvis.llm.types import (
    ImagePart,
    LLMRequest,
    Message,
    ReasoningDelta,
    StreamEnd,
    StreamEvent,
    TextDelta,
    ToolCall,
    ToolCallReady,
    ToolCallStarted,
    ToolSpec,
    Usage,
)
from tests.llm.fakes import (
    FakeAnthropicClient,
    FakeOpenAIClient,
    anthropic_final,
    chunk,
    http_response,
    tool_part,
)

TOOL = ToolSpec(
    "meteo", "Donne la météo.", {"type": "object", "properties": {"ville": {"type": "string"}}}
)


async def collect(stream: Any) -> list[StreamEvent]:
    return [event async for event in stream]


def compat(
    config: Any, client: FakeOpenAIClient, key: str | None = "cle"
) -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(config, lambda: key, client_factory=lambda k, c: client)


# ------------------------------------------------------------------ compatibles OpenAI


def test_deepseek_switches_thinking_off_by_default() -> None:
    provider = compat(DEEPSEEK, FakeOpenAIClient())
    params = provider.build_params(
        LLMRequest([Message("user", "salut")], max_tokens=50), "deepseek-flash"
    )
    assert params["extra_body"] == {"thinking": {"type": "disabled"}}
    assert params["max_tokens"] == 50
    assert params["stream_options"] == {"include_usage": True}
    thinking = provider.build_params(
        LLMRequest([Message("user", "x")], thinking=True, temperature=0.2), "deepseek-v4-pro"
    )
    assert thinking["extra_body"] == {"thinking": {"type": "enabled"}}
    assert "temperature" not in thinking  # sans effet en mode réflexion


def test_openai_uses_max_completion_tokens_and_no_thinking_switch() -> None:
    params = compat(OPENAI, FakeOpenAIClient()).build_params(
        LLMRequest([Message("user", "x")], max_tokens=10, json_mode=True, temperature=0.3), "gpt-5"
    )
    assert params["max_completion_tokens"] == 10
    assert "max_tokens" not in params
    assert "extra_body" not in params
    assert params["response_format"] == {"type": "json_object"}
    assert params["temperature"] == 0.3


def test_meta_sets_the_effort_and_leaves_room_for_hidden_reasoning() -> None:
    provider = compat(META, FakeOpenAIClient())
    quick = provider.build_params(
        LLMRequest([Message("user", "x")], max_tokens=30, temperature=0.3), "muse-spark-1.3"
    )
    assert quick["reasoning_effort"] == "minimal"
    # La réflexion cachée compte dans la limite : un titre de 30 jetons sortirait vide.
    assert quick["max_tokens"] == 30 + META.reasoning_headroom
    assert quick["temperature"] == 0.3
    assert "extra_body" not in quick
    deep = provider.build_params(
        LLMRequest([Message("user", "x")], thinking=True), "muse-spark-1.3"
    )
    assert deep["reasoning_effort"] == "high"
    assert "max_tokens" not in deep


def test_only_meta_and_baseten_write_without_an_output_cap() -> None:
    """Meta n'impose que sa fenêtre (vérifié le 2/11/2026) ; les autres ont leur maximum."""
    big = LLMRequest([Message("user", "x")], max_tokens=262_144)
    meta = compat(META, FakeOpenAIClient()).build_params(big, "muse-spark-1.3")
    assert meta["max_tokens"] == 262_144 + META.reasoning_headroom
    openai_params = compat(OPENAI, FakeOpenAIClient()).build_params(big, "gpt-5")
    assert openai_params["max_completion_tokens"] == 128_000
    deepseek = compat(DEEPSEEK, FakeOpenAIClient()).build_params(big, "deepseek-flash")
    assert deepseek["max_tokens"] == 8192


def test_meta_refuses_contributor_models() -> None:
    provider = compat(META, FakeOpenAIClient())
    with pytest.raises(InvalidRequestError, match="entraîner"):
        provider.build_params(LLMRequest([Message("user", "x")]), "muse-spark-1.3-contributor")


def test_other_providers_send_no_effort() -> None:
    params = compat(OPENROUTER, FakeOpenAIClient()).build_params(
        LLMRequest([Message("user", "x")], max_tokens=30), "un/modele"
    )
    assert "reasoning_effort" not in params
    assert params["max_tokens"] == 30


def test_tools_and_images_are_converted() -> None:
    params = compat(OPENROUTER, FakeOpenAIClient()).build_params(
        LLMRequest([Message("user", "regarde", images=(ImagePart("QUJD"),))], tools=[TOOL]),
        "google/gemma-4-31b-it:free",
    )
    assert params["tools"][0]["function"]["name"] == "meteo"
    content = params["messages"][0]["content"]
    assert content[1]["image_url"]["url"] == "data:image/png;base64,QUJD"


def test_assistant_tool_calls_and_reasoning_round_trip() -> None:
    message = Message(
        "assistant",
        "",
        tool_calls=(ToolCall("c1", "meteo", {"ville": "Lyon"}),),
        reasoning="je réfléchis",
    )
    with_reasoning = to_openai_message(message, keep_reasoning=True)
    assert with_reasoning["tool_calls"][0]["function"]["arguments"] == '{"ville": "Lyon"}'
    assert with_reasoning["reasoning_content"] == "je réfléchis"
    assert "reasoning_content" not in to_openai_message(message, keep_reasoning=False)
    tool = to_openai_message(Message("tool", "12 °C", tool_call_id="c1"), keep_reasoning=False)
    assert tool == {"role": "tool", "tool_call_id": "c1", "content": "12 °C"}


async def test_stream_text_reasoning_tools_and_usage() -> None:
    client = FakeOpenAIClient(
        [
            chunk(reasoning="hmm "),
            chunk("Bon"),
            chunk("jour"),
            chunk(tool_calls=[tool_part(0, id="c1", name="meteo", args='{"vil')]),
            chunk(tool_calls=[tool_part(0, args='le": "Paris"}')]),
            chunk(finish="tool_calls"),
            SimpleNamespace(
                choices=[],
                usage=SimpleNamespace(
                    prompt_tokens=30,
                    completion_tokens=8,
                    prompt_cache_hit_tokens=20,
                    model_extra={},
                ),
            ),
        ]
    )
    events = await collect(
        compat(DEEPSEEK, client).stream(LLMRequest([Message("user", "x")]), "deepseek-flash")
    )
    assert events[0] == ReasoningDelta("hmm ")
    assert [e.text for e in events if isinstance(e, TextDelta)] == ["Bon", "jour"]
    # Le début de l'appel est annoncé une fois, dès son nom, avant ses arguments.
    assert [e for e in events if isinstance(e, ToolCallStarted)] == [ToolCallStarted("meteo")]
    call = next(e.call for e in events if isinstance(e, ToolCallReady))
    assert call.arguments == {"ville": "Paris"}
    assert call.id == "c1"
    end = events[-1]
    assert isinstance(end, StreamEnd)
    assert end.usage == Usage(input_tokens=30, output_tokens=8, cached_input_tokens=20)
    assert end.finish_reason == "tool_calls"


async def test_openai_style_cached_tokens() -> None:
    usage = SimpleNamespace(
        prompt_tokens=5,
        completion_tokens=2,
        prompt_tokens_details=SimpleNamespace(cached_tokens=4),
        model_extra={},
    )
    client = FakeOpenAIClient(
        [chunk("ok", finish="stop"), SimpleNamespace(choices=[], usage=usage)]
    )
    events = await collect(
        compat(OPENAI, client).stream(LLMRequest([Message("user", "x")]), "gpt-5")
    )
    end = events[-1]
    assert isinstance(end, StreamEnd)
    assert end.usage.cached_input_tokens == 4


async def test_unreadable_tool_arguments_are_an_invalid_request() -> None:
    client = FakeOpenAIClient(
        [chunk(tool_calls=[tool_part(0, id="c", name="meteo", args="{cassé")])]
    )
    with pytest.raises(InvalidRequestError):
        await collect(compat(DEEPSEEK, client).stream(LLMRequest([Message("user", "x")]), "m"))


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (openai.RateLimitError("x", response=http_response(429), body=None), RateLimitedError),
        (
            openai.AuthenticationError("x", response=http_response(401), body=None),
            AuthenticationFailedError,
        ),
        (openai.APIStatusError("x", response=http_response(402), body=None), QuotaExhaustedError),
        (
            openai.InternalServerError("x", response=http_response(503), body=None),
            ProviderUnavailableError,
        ),
        (openai.BadRequestError("x", response=http_response(400), body=None), InvalidRequestError),
        (openai.APIConnectionError(request=http_response(0).request), ProviderUnavailableError),
        (RuntimeError("inattendu"), ProviderUnavailableError),
    ],
)
async def test_sdk_errors_are_classified(error: Exception, expected: type[Exception]) -> None:
    assert isinstance(classify_error(error, "deepseek"), expected)
    client = FakeOpenAIClient(error=error)
    with pytest.raises(expected):
        await collect(compat(DEEPSEEK, client).stream(LLMRequest([Message("user", "x")]), "m"))


async def test_missing_key_and_ollama_needs_none() -> None:
    assert not compat(DEEPSEEK, FakeOpenAIClient(), key=None).is_configured()
    with pytest.raises(NotConfiguredError):
        await collect(
            compat(DEEPSEEK, FakeOpenAIClient(), key=None).stream(
                LLMRequest([Message("user", "x")]), "m"
            )
        )
    assert compat(OLLAMA, FakeOpenAIClient(), key=None).is_configured()


async def test_client_is_rebuilt_when_the_key_changes() -> None:
    keys = iter(["k1", "k1", "k2"])
    built: list[str] = []

    def factory(key: str, _: Any) -> FakeOpenAIClient:
        built.append(key)
        return FakeOpenAIClient([chunk("x", finish="stop")])

    provider = OpenAICompatibleProvider(DEEPSEEK, lambda: next(keys), client_factory=factory)
    for _ in range(3):
        await collect(provider.stream(LLMRequest([Message("user", "x")]), "m"))
    assert built == ["k1", "k2"]


async def test_list_models() -> None:
    provider = compat(DEEPSEEK, FakeOpenAIClient(models=["deepseek-flash", "deepseek-v4-pro"]))
    assert await provider.list_models() == ["deepseek-flash", "deepseek-v4-pro"]


# ------------------------------------------------------------------ Anthropic


def anthropic_provider(client: FakeAnthropicClient, key: str | None = "cle") -> AnthropicProvider:
    return AnthropicProvider(lambda: key, client_factory=lambda k: client)


@pytest.mark.parametrize(
    ("model", "thinking", "expected"),
    [
        ("claude-opus-5-5", False, ("low", None)),
        ("claude-opus-5-5", True, ("high", None)),
        ("claude-sonnet-5-5", False, ("low", {"type": "between_tools"})),
        ("claude-haiku-5-5", False, ("low", {"type": "disabled"})),
    ],
)
def test_anthropic_thinking_rules(model: str, thinking: bool, expected: object) -> None:
    assert thinking_config(model, thinking) == expected


def test_anthropic_message_conversion() -> None:
    system, messages = to_anthropic_messages(
        [
            Message("system", "Tu es Jarvis."),
            Message("user", "vois", images=(ImagePart("QUJD", "image/jpeg"),)),
            Message(
                "assistant", "je regarde", tool_calls=(ToolCall("t1", "meteo", {"ville": "Lyon"}),)
            ),
            Message("tool", "12 °C", tool_call_id="t1"),
            Message("tool", "échec", tool_call_id="t2", is_error=True),
            Message("assistant", "", provider="anthropic", provider_blocks=("bloc-brut",)),
        ]
    )
    assert system == "Tu es Jarvis."
    assert messages[0]["content"][0]["source"]["media_type"] == "image/jpeg"
    assert messages[1]["content"][1] == {
        "type": "tool_use",
        "id": "t1",
        "name": "meteo",
        "input": {"ville": "Lyon"},
    }
    results = messages[2]["content"]
    assert [r["tool_use_id"] for r in results] == ["t1", "t2"]  # regroupés dans un seul message
    assert results[1]["is_error"] is True
    assert messages[3]["content"] == ["bloc-brut"]  # renvoyés tels quels au même modèle


def test_anthropic_params_fallbacks_tools_and_json() -> None:
    provider = anthropic_provider(FakeAnthropicClient())
    opus = provider.build_params(
        LLMRequest([Message("user", "x")], tools=[TOOL], json_mode=True), "claude-opus-5-5"
    )
    assert opus["betas"] == [FALLBACK_BETA]
    assert opus["fallbacks"] == "default"
    assert opus["output_config"] == {"effort": "low"}
    assert "thinking" not in opus
    assert opus["tools"][0]["eager_input_streaming"] is True
    assert "JSON" in opus["system"]
    assert "temperature" not in opus
    haiku = provider.build_params(LLMRequest([Message("user", "x")]), "claude-haiku-5-5")
    assert "fallbacks" not in haiku
    assert haiku["thinking"] == {"type": "disabled"}


async def test_anthropic_stream_text_thinking_tools_usage() -> None:
    client = FakeAnthropicClient(
        [
            SimpleNamespace(type="thinking", thinking="je pense"),
            SimpleNamespace(type="text", text="Il fait "),
            SimpleNamespace(type="text", text="beau."),
            SimpleNamespace(type="content_block_stop"),
        ],
        final=anthropic_final(
            text="Il fait beau.", tool_uses=[("t1", "meteo", {"ville": "Nice"})], cache_read=3
        ),
    )
    events = await collect(
        anthropic_provider(client).stream(LLMRequest([Message("user", "x")]), "claude-opus-5-5")
    )
    assert events[0] == ReasoningDelta("je pense")
    assert "".join(e.text for e in events if isinstance(e, TextDelta)) == "Il fait beau."
    call = next(e.call for e in events if isinstance(e, ToolCallReady))
    assert call == ToolCall("t1", "meteo", {"ville": "Nice"})
    end = events[-1]
    assert isinstance(end, StreamEnd)
    assert end.usage == Usage(input_tokens=13, output_tokens=5, cached_input_tokens=3)
    assert len(end.provider_blocks) == 2


async def test_anthropic_refusal_and_errors() -> None:
    refused = FakeAnthropicClient(final=anthropic_final(stop_reason="refusal"))
    with pytest.raises(ContentRefusedError):
        await collect(
            anthropic_provider(refused).stream(
                LLMRequest([Message("user", "x")]), "claude-opus-5-5"
            )
        )
    limited = FakeAnthropicClient(
        error=anthropic.RateLimitError("x", response=http_response(429), body=None)
    )
    with pytest.raises(RateLimitedError):
        await collect(
            anthropic_provider(limited).stream(
                LLMRequest([Message("user", "x")]), "claude-opus-5-5"
            )
        )
    broken = FakeAnthropicClient(error=ValueError("json"))
    with pytest.raises(InvalidRequestError):
        await collect(
            anthropic_provider(broken).stream(LLMRequest([Message("user", "x")]), "claude-opus-5-5")
        )
    assert isinstance(classify_anthropic(RuntimeError("x")), ProviderUnavailableError)


async def test_anthropic_key_and_models() -> None:
    assert not anthropic_provider(FakeAnthropicClient(), key=None).is_configured()
    with pytest.raises(NotConfiguredError):
        anthropic_provider(FakeAnthropicClient(), key=None).client()
    provider = anthropic_provider(FakeAnthropicClient(models=["claude-opus-5-5"]))
    assert await provider.list_models() == ["claude-opus-5-5"]


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (402, QuotaExhaustedError),
        (429, RateLimitedError),
        (403, AuthenticationFailedError),
        (500, ProviderUnavailableError),
        (404, InvalidRequestError),
    ],
)
def test_error_for_status(status: int, expected: type[Exception]) -> None:
    assert isinstance(error_for_status(status, "x", "p"), expected)


# ------------------------------------------------------------------ fournisseur de test


async def test_fake_provider_scripts() -> None:
    provider = FakeProvider(
        script=lambda r: FakeReply(
            text="abcdefghijklmnop", reasoning="r", tool_calls=[ToolCall("1", "t", {})]
        ),
        chunk_chars=5,
    )
    events = await collect(provider.stream(LLMRequest([Message("user", "x")]), "m"))
    assert [e.text for e in events if isinstance(e, TextDelta)] == ["abcde", "fghij", "klmno", "p"]
    assert isinstance(events[0], ReasoningDelta)
    assert provider.requests[0][1] == "m"
    assert await provider.list_models() == ["fake-model"]


def test_a_text_after_tool_results_joins_their_user_message() -> None:
    # L'arrêt au nombre d'étapes : « dis où tu en es » suit des résultats d'outils.
    call = ToolCall("t1", "executer_commande", {"commande": "ls"})
    _system, out = to_anthropic_messages(
        [
            Message("user", "Vérifie tout."),
            Message("assistant", "", tool_calls=(call,)),
            Message("tool", "a.txt", tool_call_id="t1"),
            Message("user", "Dis où tu en es."),
        ]
    )
    assert [m["role"] for m in out] == ["user", "assistant", "user"]
    assert [b["type"] for b in out[-1]["content"]] == ["tool_result", "text"]
