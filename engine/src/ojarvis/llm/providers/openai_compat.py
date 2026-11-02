"""Adaptateur générique pour les API compatibles OpenAI : DeepSeek, OpenRouter, OpenAI, Meta,
Baseten, Ollama.

Chaque fournisseur ne diffère que par son adresse, sa clé et quelques options
(réflexion de DeepSeek, nom du paramètre de longueur chez OpenAI).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

import openai

from ojarvis.llm.errors import (
    InvalidRequestError,
    LLMError,
    NotConfiguredError,
    ProviderUnavailableError,
    error_for_status,
)
from ojarvis.llm.jsonparse import JSONExtractionError, loads_arguments
from ojarvis.llm.types import (
    LLMRequest,
    Message,
    ReasoningDelta,
    StreamEnd,
    StreamEvent,
    TextDelta,
    ToolCall,
    ToolCallReady,
    ToolCallStarted,
    Usage,
)

KeyGetter = Callable[[], str | None]


@dataclass(frozen=True)
class CompatConfig:
    name: str
    base_url: str | None
    local: bool = False
    #: DeepSeek : `thinking: {type: enabled|disabled}` dans le corps de la requête.
    thinking_switch: bool = False
    #: OpenAI : `max_completion_tokens` remplace `max_tokens`.
    max_tokens_param: str = "max_tokens"
    #: Meta : la réflexion ne se coupe pas, elle se règle par `reasoning_effort`
    #: (effort sans réflexion demandée, puis avec).
    reasoning_effort: tuple[str, str] | None = None
    #: Jetons ajoutés à la limite quand la réflexion cachée y est comptée (Meta) : sans
    #: eux, une limite courte (un titre : 30 jetons) part dans la réflexion, réponse vide.
    reasoning_headroom: int = 0
    #: Meta : en mode Contributor (`-contributor`), les requêtes servent à entraîner ses
    #: modèles. Ces identifiants sont refusés.
    refused_model_suffix: str | None = None
    #: Le plus long que ce fournisseur accepte d'écrire (réflexion comprise) ; None : aucune
    #: autre limite que la fenêtre du modèle (Meta, Baseten, vérifié le 2/11/2026).
    max_output_tokens: int | None = 8192
    #: Ollama n'a pas de clé : une valeur factice suffit.
    needs_key: bool = True
    timeout_s: float = 45.0
    headers: dict[str, str] = field(default_factory=dict)


DEEPSEEK = CompatConfig("deepseek", "https://api.deepseek.com", thinking_switch=True)
OPENROUTER = CompatConfig(
    "openrouter", "https://openrouter.ai/api/v1", headers={"X-Title": "O-jarvis"}
)
OPENAI = CompatConfig(
    "openai", None, max_tokens_param="max_completion_tokens", max_output_tokens=128_000
)
# Effort « minimal » : premier mot en 0,8 s au lieu de 1,6 s à l'essai (26/10/2026).
META = CompatConfig(
    "meta",
    "https://api.meta.ai/v1",
    reasoning_effort=("minimal", "high"),
    reasoning_headroom=2048,
    max_output_tokens=None,
    refused_model_suffix="-contributor",
)
#: Muse Spark Open, le modèle ouvert de Meta, servi par Baseten (hackathon) : la réflexion se
#: règle comme chez Muse Spark (« minimal » : 0,8 s à l'essai, le 27/10/2026).
BASETEN = CompatConfig(
    "baseten",
    "https://inference.baseten.co/v1",
    reasoning_effort=("minimal", "high"),
    reasoning_headroom=2048,
    max_output_tokens=None,
)
OLLAMA = CompatConfig(
    "ollama",
    "http://127.0.0.1:11434/v1",
    local=True,
    needs_key=False,
    timeout_s=180.0,
    max_output_tokens=None,
)


def to_openai_message(message: Message, *, keep_reasoning: bool) -> dict[str, Any]:
    if message.role == "tool":
        return {"role": "tool", "tool_call_id": message.tool_call_id, "content": message.content}
    if message.role == "assistant":
        data: dict[str, Any] = {"role": "assistant", "content": message.content or None}
        if message.tool_calls:
            data["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": call.raw_arguments
                        or json.dumps(call.arguments, ensure_ascii=False),
                    },
                }
                for call in message.tool_calls
            ]
        if keep_reasoning and message.reasoning:
            data["reasoning_content"] = message.reasoning
        return data
    if message.role == "user" and message.images:
        parts: list[dict[str, Any]] = [{"type": "text", "text": message.content}]
        parts.extend(
            {
                "type": "image_url",
                "image_url": {"url": f"data:{image.media_type};base64,{image.data_base64}"},
            }
            for image in message.images
        )
        return {"role": "user", "content": parts}
    return {"role": message.role, "content": message.content}


def classify_error(exc: Exception, provider: str) -> LLMError:
    """Traduit une erreur du SDK en erreur classée (pour décider d'un repli)."""
    text = f"{type(exc).__name__}: {exc}"[:300]
    if isinstance(exc, openai.APIStatusError):
        return error_for_status(exc.status_code, text, provider)
    if isinstance(exc, openai.APIConnectionError):
        return ProviderUnavailableError(text, provider=provider)
    return ProviderUnavailableError(text, provider=provider)


def _extra(delta: Any, *names: str) -> str | None:
    for name in names:
        value = getattr(delta, name, None)
        if value is None and getattr(delta, "model_extra", None):
            value = delta.model_extra.get(name)
        if isinstance(value, str) and value:
            return value
    return None


class OpenAICompatibleProvider:
    def __init__(
        self,
        config: CompatConfig,
        key_getter: KeyGetter,
        *,
        client_factory: Callable[[str, CompatConfig], Any] | None = None,
    ) -> None:
        self.config = config
        self.name = config.name
        self.local = config.local
        self._key_getter = key_getter
        self._factory = client_factory or self._default_client
        self._client: Any = None
        self._client_key: str | None = None

    @staticmethod
    def _default_client(key: str, config: CompatConfig) -> Any:
        return openai.AsyncOpenAI(
            api_key=key,
            base_url=config.base_url,
            timeout=config.timeout_s,
            max_retries=0,
            default_headers=config.headers or None,
        )

    def _key(self) -> str | None:
        return self._key_getter() if self.config.needs_key else "ollama"

    def is_configured(self) -> bool:
        return bool(self._key())

    def client(self) -> Any:
        key = self._key()
        if not key:
            raise NotConfiguredError(f"{self.name} : aucune clé", provider=self.name)
        if self._client is None or key != self._client_key:
            self._client = self._factory(key, self.config)
            self._client_key = key
        return self._client

    def build_params(self, request: LLMRequest, model: str) -> dict[str, Any]:
        suffix = self.config.refused_model_suffix
        if suffix and model.endswith(suffix):
            raise InvalidRequestError(
                f"« {model} » est refusé : ce mode laisse le fournisseur s'entraîner sur tes "
                "requêtes.",
                provider=self.name,
            )
        keep_reasoning = self.config.thinking_switch and request.thinking
        params: dict[str, Any] = {
            "model": model,
            "messages": [
                to_openai_message(m, keep_reasoning=keep_reasoning) for m in request.messages
            ],
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if request.tools:
            params["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.parameters,
                    },
                }
                for tool in request.tools
            ]
        if request.json_mode:
            params["response_format"] = {"type": "json_object"}
        if request.max_tokens:
            limit = request.max_tokens + self.config.reasoning_headroom
            if self.config.max_output_tokens is not None:
                limit = min(limit, self.config.max_output_tokens)
            params[self.config.max_tokens_param] = limit
        if request.temperature is not None and not request.thinking:
            params["temperature"] = request.temperature
        if self.config.thinking_switch:
            mode = "enabled" if request.thinking else "disabled"
            params["extra_body"] = {"thinking": {"type": mode}}
        if self.config.reasoning_effort:
            quick, deep = self.config.reasoning_effort
            params["reasoning_effort"] = deep if request.thinking else quick
        return params

    async def stream(self, request: LLMRequest, model: str) -> AsyncIterator[StreamEvent]:
        params = self.build_params(request, model)
        calls: dict[int, dict[str, str]] = {}
        usage = Usage()
        finish: str | None = None
        try:
            response = await self.client().chat.completions.create(**params)
            async for chunk in response:
                if getattr(chunk, "usage", None):
                    usage = self._usage(chunk.usage)
                for choice in chunk.choices or []:
                    for event in self._choice_events(choice, calls):
                        yield event
                    if choice.finish_reason:
                        finish = choice.finish_reason
        except LLMError:
            raise
        except Exception as exc:
            raise classify_error(exc, self.name) from exc
        for event in self._tool_calls(calls):
            yield event
        yield StreamEnd(usage, finish)

    @staticmethod
    def _choice_events(choice: Any, calls: dict[int, dict[str, str]]) -> list[StreamEvent]:
        """Événements d'un morceau de flux ; accumule les fragments d'appels d'outil."""
        delta = choice.delta
        if delta is None:
            return []
        events: list[StreamEvent] = []
        reasoning = _extra(delta, "reasoning_content", "reasoning")
        if reasoning:
            events.append(ReasoningDelta(reasoning))
        if delta.content:
            events.append(TextDelta(delta.content))
        for part in delta.tool_calls or []:
            slot = calls.setdefault(part.index, {"id": "", "name": "", "args": ""})
            if part.id:
                slot["id"] = part.id
            if part.function is not None:
                if part.function.name and not slot["name"]:
                    events.append(ToolCallStarted(part.function.name))
                slot["name"] += part.function.name or ""
                slot["args"] += part.function.arguments or ""
        return events

    def _tool_calls(self, calls: dict[int, dict[str, str]]) -> list[StreamEvent]:
        events: list[StreamEvent] = []
        for index in sorted(calls):
            slot = calls[index]
            try:
                arguments = loads_arguments(slot["args"])
            except JSONExtractionError as exc:
                raise InvalidRequestError(
                    f"arguments d'outil illisibles : {exc}", provider=self.name
                ) from exc
            events.append(
                ToolCallReady(
                    ToolCall(
                        id=slot["id"] or f"appel_{index}",
                        name=slot["name"],
                        arguments=arguments,
                        raw_arguments=slot["args"],
                    )
                )
            )
        return events

    @staticmethod
    def _usage(raw: Any) -> Usage:
        cached = getattr(raw, "prompt_cache_hit_tokens", None)
        if cached is None and getattr(raw, "model_extra", None):
            cached = raw.model_extra.get("prompt_cache_hit_tokens")
        details = getattr(raw, "prompt_tokens_details", None)
        if cached is None and details is not None:
            cached = getattr(details, "cached_tokens", None)
        return Usage(
            input_tokens=int(getattr(raw, "prompt_tokens", 0) or 0),
            output_tokens=int(getattr(raw, "completion_tokens", 0) or 0),
            cached_input_tokens=int(cached or 0),
        )

    async def list_models(self) -> list[str]:
        try:
            page = await self.client().models.list()
            return (
                [m.id async for m in page]
                if hasattr(page, "__aiter__")
                else [m.id for m in page.data]
            )
        except LLMError:
            raise
        except Exception as exc:
            raise classify_error(exc, self.name) from exc
