"""Adaptateur Anthropic (SDK officiel `anthropic` 1.x, Messages API en flux).

Règles des modèles actuels (doc officielle, octobre 2026) :
- Opus 5.5 : la réflexion ne se coupe pas, seul l'effort la règle (`low` pour la voix) ;
- Sonnet 5.5 : `between_tools` coupe la réflexion ; Haiku 5.5 : `disabled` (effort ≤ high) ;
- pas d'appel d'outil forcé, pas de pré-remplissage de la réponse, pas de température ;
- repli côté serveur en cas de refus (`fallbacks="default"`) sur Opus 5.5 et Sonnet 5.5 ;
- les blocs de la réponse (réflexion, appels d'outil) se renvoient tels quels au même modèle.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any

import anthropic

from ojarvis.llm.errors import (
    ContentRefusedError,
    InvalidRequestError,
    LLMError,
    NotConfiguredError,
    ProviderUnavailableError,
    error_for_status,
)
from ojarvis.llm.types import (
    LLMRequest,
    Message,
    ReasoningDelta,
    StreamEnd,
    StreamEvent,
    TextDelta,
    ToolCall,
    ToolCallReady,
    Usage,
)

KeyGetter = Callable[[], str | None]

FALLBACK_BETA = "server-side-fallback-2026-07-01"
#: Modèles qui acceptent le repli côté serveur sous sa forme « default ».
FALLBACK_MODELS = ("claude-opus-5-5", "claude-sonnet-5-5")
JSON_INSTRUCTION = "Réponds uniquement par un objet JSON valide, sans texte autour."
#: Le plus long qu'une réponse de ce fournisseur peut écrire.
MAX_OUTPUT_TOKENS = 64_000


def thinking_config(model: str, thinking: bool) -> tuple[str, dict[str, str] | None]:
    """(effort, paramètre `thinking`) selon le modèle et le besoin de réflexion."""
    if thinking:
        return "high", None
    if model.startswith("claude-sonnet-5-5"):
        return "low", {"type": "between_tools"}
    if model.startswith("claude-haiku"):
        return "low", {"type": "disabled"}
    return "low", None


def to_anthropic_messages(messages: list[Message]) -> tuple[str, list[dict[str, Any]]]:
    """Sépare le système et regroupe les résultats d'outils dans un message utilisateur."""
    system_parts: list[str] = []
    out: list[dict[str, Any]] = []
    for message in messages:
        if message.role == "system":
            system_parts.append(message.content)
            continue
        if message.role == "tool":
            block = {
                "type": "tool_result",
                "tool_use_id": message.tool_call_id,
                "content": message.content,
                "is_error": message.is_error,
            }
            if (
                out
                and out[-1]["role"] == "user"
                and isinstance(out[-1]["content"], list)
                and all(
                    isinstance(b, dict) and b.get("type") == "tool_result"
                    for b in out[-1]["content"]
                )
            ):
                out[-1]["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
            continue
        if message.role == "assistant":
            if message.provider == "anthropic" and message.provider_blocks:
                out.append({"role": "assistant", "content": list(message.provider_blocks)})
                continue
            blocks: list[dict[str, Any]] = []
            if message.content:
                blocks.append({"type": "text", "text": message.content})
            blocks.extend(
                {"type": "tool_use", "id": call.id, "name": call.name, "input": call.arguments}
                for call in message.tool_calls
            )
            out.append({"role": "assistant", "content": blocks or message.content})
            continue
        if message.images:
            content: list[dict[str, Any]] = [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": image.media_type,
                        "data": image.data_base64,
                    },
                }
                for image in message.images
            ]
            content.append({"type": "text", "text": message.content})
            out.append({"role": "user", "content": content})
        elif out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
            # Après des résultats d'outils (l'arrêt au nombre d'étapes) : un seul message.
            out[-1]["content"].append({"type": "text", "text": message.content})
        else:
            out.append({"role": "user", "content": message.content})
    return "\n\n".join(p for p in system_parts if p), out


def classify_error(exc: Exception) -> LLMError:
    text = f"{type(exc).__name__}: {exc}"[:300]
    if isinstance(exc, anthropic.APIStatusError):
        return error_for_status(exc.status_code, text, "anthropic")
    return ProviderUnavailableError(text, provider="anthropic")


class AnthropicProvider:
    name = "anthropic"
    local = False

    def __init__(
        self, key_getter: KeyGetter, *, client_factory: Callable[[str], Any] | None = None
    ) -> None:
        self._key_getter = key_getter
        self._factory = client_factory or (
            lambda key: anthropic.AsyncAnthropic(api_key=key, timeout=60.0, max_retries=0)
        )
        self._client: Any = None
        self._client_key: str | None = None

    def is_configured(self) -> bool:
        return bool(self._key_getter())

    def client(self) -> Any:
        key = self._key_getter()
        if not key:
            raise NotConfiguredError("anthropic : aucune clé", provider=self.name)
        if self._client is None or key != self._client_key:
            self._client = self._factory(key)
            self._client_key = key
        return self._client

    def build_params(self, request: LLMRequest, model: str) -> dict[str, Any]:
        system, messages = to_anthropic_messages(request.messages)
        if request.json_mode:
            system = f"{system}\n\n{JSON_INSTRUCTION}".strip()
        effort, thinking = thinking_config(model, request.thinking)
        params: dict[str, Any] = {
            "model": model,
            "max_tokens": min(request.max_tokens or 16_000, MAX_OUTPUT_TOKENS),
            "messages": messages,
            "output_config": {"effort": effort},
        }
        if system:
            params["system"] = system
        if thinking is not None:
            params["thinking"] = thinking
        if request.tools:
            params["tools"] = [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": tool.parameters,
                    "eager_input_streaming": True,
                }
                for tool in request.tools
            ]
        if model in FALLBACK_MODELS:
            params["betas"] = [FALLBACK_BETA]
            params["fallbacks"] = "default"
        return params

    async def stream(self, request: LLMRequest, model: str) -> AsyncIterator[StreamEvent]:
        params = self.build_params(request, model)
        try:
            async with self.client().beta.messages.stream(**params) as stream:
                async for event in stream:
                    kind = getattr(event, "type", "")
                    if kind == "text":
                        yield TextDelta(event.text)
                    elif kind == "thinking" and getattr(event, "thinking", ""):
                        yield ReasoningDelta(event.thinking)
                final = await stream.get_final_message()
        except LLMError:
            raise
        except ValueError as exc:
            # JSON d'appel d'outil illisible : on ne lance rien, la requête est à refaire.
            raise InvalidRequestError(
                f"arguments d'outil illisibles : {exc}", provider=self.name
            ) from exc
        except Exception as exc:
            raise classify_error(exc) from exc
        if final.stop_reason == "refusal":
            raise ContentRefusedError("refus du modèle", provider=self.name)
        for block in final.content:
            if getattr(block, "type", "") == "tool_use":
                arguments = block.input if isinstance(block.input, dict) else {}
                yield ToolCallReady(ToolCall(id=block.id, name=block.name, arguments=arguments))
        usage = final.usage
        yield StreamEnd(
            Usage(
                input_tokens=int(getattr(usage, "input_tokens", 0) or 0)
                + int(getattr(usage, "cache_read_input_tokens", 0) or 0),
                output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
                cached_input_tokens=int(getattr(usage, "cache_read_input_tokens", 0) or 0),
            ),
            final.stop_reason,
            provider_blocks=tuple(final.content),
        )

    async def list_models(self) -> list[str]:
        try:
            page = await self.client().models.list()
            return [m.id async for m in page]
        except LLMError:
            raise
        except Exception as exc:
            raise classify_error(exc) from exc
