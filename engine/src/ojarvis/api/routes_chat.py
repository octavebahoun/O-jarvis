"""Chat en flux (SSE), conversations, mémoire et état des modèles."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ojarvis.agent.reports import RanCommand
from ojarvis.api.deps import ServicesDep
from ojarvis.chat.service import ChatEvent
from ojarvis.chrome.page import BrowserPage, PageElement
from ojarvis.conversations.store import ConversationRecord, MessageRecord
from ojarvis.core.errors import EngineError, NotFoundError
from ojarvis.llm.providers.registry import OllamaProvider
from ojarvis.memory.store import MemoryKind, MemoryRecord
from ojarvis.tools.guards import resolve_user_path, shown

router = APIRouter(prefix="/v1")


# ---------------------------------------------------------------------- chat


class RanIn(BaseModel):
    """Une commande sudo proposée par Jarvis, que l'utilisateur a lancée lui-même."""

    model_config = ConfigDict(extra="forbid")

    command: str = Field(min_length=1, max_length=4000)
    code: int = Field(ge=-255, le=255)
    output: str = Field(default="", max_length=20_000)


class ElementIn(BaseModel):
    """Un élément de l'onglet sur lequel Jarvis peut agir, numéroté par l'extension."""

    model_config = ConfigDict(extra="forbid")

    ref: int = Field(ge=1, le=100_000)
    kind: Literal["lien", "bouton", "champ", "liste", "case"]
    label: str = Field(default="", max_length=200)
    href: str = Field(default="", max_length=2000)


class PageIn(BaseModel):
    """Chrome : l'onglet ouvert, lu par l'extension (jamais ses cookies ni ses champs)."""

    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=1, max_length=2000, pattern=r"^https?://")
    title: str = Field(default="", max_length=500)
    text: str = Field(default="", max_length=100_000)
    selection: str = Field(default="", max_length=8000)
    elements: list[ElementIn] = Field(default_factory=list, max_length=300)


class ChatIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=8000)
    conversation_id: str | None = Field(default=None, max_length=32)
    channel: Literal["app", "voice", "shell", "chrome"] = "app"
    #: Mode shell : le dossier de la session (~/… ou chemin absolu dans ton dossier).
    folder: str | None = Field(default=None, max_length=1000)
    #: Mode shell : les plans validés dans le terminal ; leurs résultats reviennent au modèle.
    #: Un seul identifiant est aussi accepté.
    after_plan: list[Annotated[str, Field(max_length=32)]] | None = Field(
        default=None, max_length=10
    )

    #: Mode shell : une commande sudo que l'utilisateur a lancée lui-même ; sa sortie revient.
    ran: RanIn | None = None

    #: Chrome : l'onglet ouvert à côté du panneau ; il va avec ce canal, et seulement lui.
    page: PageIn | None = None

    @field_validator("after_plan", mode="before")
    @classmethod
    def _one_or_more(cls, value: Any) -> Any:
        return [value] if isinstance(value, str) else value

    @model_validator(mode="after")
    def _page_in_chrome(self) -> ChatIn:
        if self.page is not None and self.channel != "chrome":
            raise ValueError("L'onglet va avec le panneau de Chrome, et seulement lui.")
        return self


#: Fins de ligne pour httpx (qui coupe comme `str.splitlines`), permises telles quelles dans
#: une chaîne JSON : échappées, une ligne de données ne se coupe jamais en deux.
LINE_BREAKS = str.maketrans({"\u2028": "\\u2028", "\u2029": "\\u2029", "\x85": "\\u0085"})


def sse(event: ChatEvent) -> str:
    data = json.dumps(event.data, ensure_ascii=False).translate(LINE_BREAKS)
    return f"event: {event.type}\ndata: {data}\n\n"


def _page(page: PageIn | None) -> BrowserPage | None:
    if page is None:
        return None
    elements = tuple(PageElement(**e.model_dump()) for e in page.elements)
    return BrowserPage(page.url, page.title, page.text, page.selection, elements)


@router.post("/chat")
async def chat(body: ChatIn, services: ServicesDep) -> StreamingResponse:
    """Réponse en flux : événements start, context, route, delta…, puis done ou error."""
    folder = None
    if body.folder is not None:
        # Le dossier de la session, dit comme Jarvis le dit (« ~/projet ») : vérifié ici (dans
        # ton dossier personnel, hors dossiers de secrets), et sans ton nom d'utilisateur.
        home = services.config.user_home or Path.home()
        path = resolve_user_path(body.folder, home)
        if not path.is_dir():
            raise NotFoundError(f"Dossier introuvable : {body.folder}.")
        folder = shown(path, home)
    stream = services.chat.respond(
        text=body.text,
        conversation_id=body.conversation_id,
        channel=body.channel,
        shell_folder=folder,
        after_plan=body.after_plan,
        ran=RanCommand(body.ran.command, body.ran.code, body.ran.output) if body.ran else None,
        page=_page(body.page),
    )
    # Les erreurs de validation (conversation inconnue, texte vide) arrivent avant le flux.
    first = await anext(stream)

    async def events() -> AsyncIterator[str]:
        yield sse(first)
        try:
            async for event in stream:
                yield sse(event)
        except EngineError as error:
            yield sse(ChatEvent("error", {"message": error.message}))
        finally:
            await stream.aclose()

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------- conversations


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    channel: str
    created_at: datetime
    updated_at: datetime
    message_count: int
    preview: str


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    role: str
    content: str
    status: str
    created_at: datetime
    provider: str | None
    model: str | None
    tokens_in: int | None
    tokens_out: int | None
    cost_usd: float | None
    latency_ms: float | None
    trace_id: str | None
    #: Outils utilisés et plans proposés pendant ce tour.
    meta: dict[str, Any] = Field(default_factory=dict)


class ConversationDetailOut(ConversationOut):
    messages: list[MessageOut]


class ConversationPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)


def _conversation(record: ConversationRecord) -> ConversationOut:
    return ConversationOut.model_validate(record)


@router.get("/conversations")
async def list_conversations(
    services: ServicesDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    before: datetime | None = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
) -> list[ConversationOut]:
    rows = await services.conversations.recent(limit=limit, before=before, query=q)
    return [_conversation(r) for r in rows]


@router.post("/conversations", status_code=201)
async def create_conversation(services: ServicesDep) -> ConversationOut:
    record = await services.conversations.create("app")
    await services.bus.publish("conversation.created", {"id": record.id, "channel": "app"})
    return _conversation(record)


@router.get("/conversations/{conversation_id}")
async def get_conversation(conversation_id: str, services: ServicesDep) -> ConversationDetailOut:
    record = await services.conversations.get(conversation_id)
    messages: list[MessageRecord] = await services.conversations.messages(
        conversation_id, limit=500
    )
    return ConversationDetailOut(
        **ConversationOut.model_validate(record).model_dump(),
        messages=[MessageOut.model_validate(m) for m in messages],
    )


@router.patch("/conversations/{conversation_id}")
async def rename_conversation(
    conversation_id: str, body: ConversationPatch, services: ServicesDep
) -> ConversationOut:
    record = await services.conversations.rename(conversation_id, body.title)
    await services.bus.publish("conversation.updated", {"id": conversation_id})
    return _conversation(record)


@router.delete("/conversations/{conversation_id}", status_code=204)
async def delete_conversation(conversation_id: str, services: ServicesDep) -> Response:
    await services.conversations.delete(conversation_id)
    await services.bus.publish("conversation.deleted", {"id": conversation_id})
    return Response(status_code=204)


# ---------------------------------------------------------------------- mémoire


class MemoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: str
    content: str
    source: str
    confidence: float
    pinned: bool
    created_at: datetime
    updated_at: datetime
    last_used_at: datetime | None
    use_count: int


class MemoryIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: MemoryKind
    content: str = Field(min_length=1, max_length=1000)
    pinned: bool = False


class MemoryPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: MemoryKind | None = None
    content: str | None = Field(default=None, min_length=1, max_length=1000)
    pinned: bool | None = None


class MemoryHitOut(BaseModel):
    item: MemoryOut
    score: float
    via: str


def _memory(record: MemoryRecord) -> MemoryOut:
    return MemoryOut.model_validate(record)


@router.get("/memory")
async def list_memory(
    services: ServicesDep,
    kind: MemoryKind | None = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
) -> list[MemoryOut]:
    return [_memory(r) for r in await services.memory.items(kind=kind, query=q)]


@router.post("/memory", status_code=201)
async def add_memory(body: MemoryIn, services: ServicesDep) -> MemoryOut:
    record, _ = await services.memory.add(
        body.kind, body.content, source="manuel", pinned=body.pinned
    )
    return _memory(record)


@router.patch("/memory/{item_id}")
async def update_memory(item_id: int, body: MemoryPatch, services: ServicesDep) -> MemoryOut:
    record = await services.memory.update(
        item_id, content=body.content, kind=body.kind, pinned=body.pinned
    )
    return _memory(record)


@router.delete("/memory/{item_id}", status_code=204)
async def delete_memory(item_id: int, services: ServicesDep) -> Response:
    await services.memory.delete(item_id)
    return Response(status_code=204)


@router.get("/memory/search")
async def search_memory(
    services: ServicesDep, q: Annotated[str, Query(min_length=1, max_length=300)]
) -> list[MemoryHitOut]:
    hits = await services.memory.search(q, k=10)
    return [MemoryHitOut(item=_memory(h.record), score=h.score, via=h.via) for h in hits]


@router.get("/memory/stats")
async def memory_stats(services: ServicesDep) -> dict[str, object]:
    return {
        "counts": await services.memory.count(),
        "embeddings": services.embedder.status,
        "vector_index": "sqlite-vec" if services.memory.index.native else "numpy",
    }


# ---------------------------------------------------------------------- modèles


class ProviderStatusOut(BaseModel):
    provider: str
    configured: bool
    local: bool
    available: bool
    degraded: bool
    last_error: str | None


class ModelOut(BaseModel):
    provider: str
    model: str
    label: str
    tools: bool
    vision: bool
    local: bool
    price_in: float | None
    price_out: float | None


@router.get("/llm/status")
async def llm_status(services: ServicesDep) -> list[ProviderStatusOut]:
    ollama = services.providers.get("ollama")
    if isinstance(ollama, OllamaProvider):
        await ollama.refresh(services.http)
    return [ProviderStatusOut.model_validate(s) for s in services.router.status()]


@router.get("/llm/models")
async def llm_models(services: ServicesDep) -> list[ModelOut]:
    return [
        ModelOut(
            provider=s.provider,
            model=s.model,
            label=s.label,
            tools=s.tools,
            vision=s.vision,
            local=s.local,
            price_in=s.price.input if s.price else None,
            price_out=s.price.output if s.price else None,
        )
        for s in services.catalog.all()
    ]
