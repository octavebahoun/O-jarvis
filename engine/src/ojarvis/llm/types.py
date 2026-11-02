"""Types communs à tous les fournisseurs de modèles : messages, outils, flux, résultat."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Role = Literal["system", "user", "assistant", "tool"]

#: Les types de tâche que le routeur sait servir.
TaskType = Literal["chat", "voice", "reasoning", "extract", "title", "vision", "review"]
TASK_TYPES: tuple[TaskType, ...] = (
    "chat",
    "voice",
    "reasoning",
    "extract",
    "title",
    "vision",
    "review",
)


@dataclass(frozen=True, slots=True)
class ImagePart:
    data_base64: str
    media_type: str = "image/png"


@dataclass(frozen=True, slots=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]
    raw_arguments: str = ""


@dataclass(slots=True)
class Message:
    role: Role
    content: str = ""
    images: tuple[ImagePart, ...] = ()
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None
    is_error: bool = False
    #: Réflexion renvoyée par le modèle (DeepSeek l'exige en retour quand il y a des outils).
    reasoning: str | None = None
    #: Blocs bruts d'un fournisseur, renvoyés tels quels au même modèle (Anthropic).
    provider_blocks: tuple[Any, ...] = ()
    provider: str | None = None


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(slots=True)
class LLMRequest:
    messages: list[Message]
    tools: list[ToolSpec] = field(default_factory=list)
    json_mode: bool = False
    max_tokens: int | None = None
    temperature: float | None = None
    #: Réflexion longue : plus lente, plus fiable. Coupée pour la voix et le chat courant.
    thinking: bool = False


@dataclass(frozen=True, slots=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0


@dataclass(frozen=True, slots=True)
class TextDelta:
    text: str


@dataclass(frozen=True, slots=True)
class ReasoningDelta:
    text: str


@dataclass(frozen=True, slots=True)
class ToolCallStarted:
    """Le modèle commence un appel d'outil : ses arguments (un fichier entier, parfois)
    arrivent ensuite, sans rien d'autre à montrer pendant ce temps."""

    name: str


@dataclass(frozen=True, slots=True)
class ToolCallReady:
    call: ToolCall


@dataclass(frozen=True, slots=True)
class StreamEnd:
    usage: Usage
    finish_reason: str | None
    provider_blocks: tuple[Any, ...] = ()


StreamEvent = TextDelta | ReasoningDelta | ToolCallStarted | ToolCallReady | StreamEnd


@dataclass(frozen=True, slots=True)
class RouteInfo:
    """Annonce, en tête de flux, du fournisseur et du modèle retenus."""

    provider: str
    model: str
    fallback_from: str | None = None


@dataclass(slots=True)
class LLMResult:
    text: str
    reasoning: str
    tool_calls: list[ToolCall]
    usage: Usage
    finish_reason: str | None
    provider: str
    model: str
    cost_usd: float
    latency_ms: float
    ttft_ms: float | None
    provider_blocks: tuple[Any, ...] = ()

    def as_message(self) -> Message:
        """Le message assistant à rejouer dans la suite de la conversation."""
        return Message(
            role="assistant",
            content=self.text,
            tool_calls=tuple(self.tool_calls),
            reasoning=self.reasoning or None,
            provider_blocks=self.provider_blocks,
            provider=self.provider,
        )
