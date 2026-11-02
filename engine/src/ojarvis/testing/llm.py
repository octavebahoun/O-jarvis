"""Un routeur de modèles pour les tests : fournisseurs factices, horloge réglable."""

from __future__ import annotations

from ojarvis.events.bus import EventBus
from ojarvis.llm.catalog import Catalog
from ojarvis.llm.policy import Route
from ojarvis.llm.providers.fake import FakeProvider
from ojarvis.llm.router import ModelRouter
from ojarvis.llm.types import LLMRequest, Message, TaskType
from ojarvis.tracing.tracer import Tracer

POLICY: dict[TaskType, tuple[Route, ...]] = {
    "chat": (
        Route("deepseek", "deepseek-flash"),
        Route("openrouter", "google/gemma-4-26b-a4b-it:free"),
        Route("ollama", "ministral-3:3b"),
    ),
    "reasoning": (Route("deepseek", "deepseek-v4-pro", thinking=True),),
    "vision": (Route("deepseek", "deepseek-flash"), Route("ollama", "ministral-3:3b")),
}


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def request(text: str = "bonjour") -> LLMRequest:
    return LLMRequest([Message("user", text)])


def make_router(
    *,
    deepseek: FakeProvider | None = None,
    openrouter: FakeProvider | None = None,
    ollama: FakeProvider | None = None,
    meta: FakeProvider | None = None,
    baseten: FakeProvider | None = None,
    bus: EventBus | None = None,
    overrides: dict[str, str] | None = None,
    profile: str = "auto",
    clock: Clock | None = None,
    tracer: Tracer | None = None,
) -> ModelRouter:
    providers = {
        "deepseek": deepseek or FakeProvider("deepseek"),
        "openrouter": openrouter or FakeProvider("openrouter"),
        "ollama": ollama or FakeProvider("ollama", local=True),
        "meta": meta or FakeProvider("meta"),
        "baseten": baseten or FakeProvider("baseten"),
    }
    return ModelRouter(
        providers,
        Catalog(),
        bus=bus,
        tracer=tracer,
        overrides=lambda: overrides or {},
        profile=lambda: profile,
        policy=POLICY,
        clock=clock or Clock(),
    )
