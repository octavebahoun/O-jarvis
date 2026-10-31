"""Fabrique de l'application FastAPI du moteur."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from ojarvis import __version__
from ojarvis.api import (
    routes_agent,
    routes_agents,
    routes_automations,
    routes_chat,
    routes_chrome,
    routes_core,
    routes_graph,
    routes_integrations,
    routes_local_models,
    routes_mcp,
    routes_secrets,
    routes_skills,
    routes_stream,
    routes_suggestions,
    routes_voice,
)
from ojarvis.config import EngineConfig
from ojarvis.core.errors import install_error_handlers
from ojarvis.llm.providers.base import Provider
from ojarvis.memory.embeddings import Embedder
from ojarvis.security.auth import CorrelationIdMiddleware, TokenAuthMiddleware
from ojarvis.security.redaction import REDACTOR
from ojarvis.security.secrets import SecretBackend
from ojarvis.services import Services

PUBLIC_PATHS = frozenset({"/health"})


def create_app(
    config: EngineConfig,
    *,
    token: str,
    secret_backend: SecretBackend | None = None,
    http_transport: httpx.AsyncBaseTransport | None = None,
    embedder: Embedder | None = None,
    providers: dict[str, Provider] | None = None,
    discover: bool = True,
    on_ready: Callable[[], None] | None = None,
) -> FastAPI:
    if len(token) < 32:
        raise ValueError("jeton trop court")
    REDACTOR.add_value(token)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        services = await Services.start(
            config,
            secret_backend=secret_backend,
            http_transport=http_transport,
            embedder=embedder,
            providers=providers,
            discover=discover,
        )
        app.state.services = services
        if on_ready is not None:
            on_ready()
        try:
            yield
        finally:
            await services.stop()

    app = FastAPI(
        title="O-jarvis — moteur",
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs" if config.dev_mode else None,
        redoc_url=None,
        openapi_url="/openapi.json" if config.dev_mode else None,
    )
    app.state.token = token
    install_error_handlers(app)
    app.include_router(routes_core.public)
    app.include_router(routes_core.router)
    app.include_router(routes_secrets.router)
    app.include_router(routes_stream.router)
    app.include_router(routes_graph.router)
    app.include_router(routes_chat.router)
    app.include_router(routes_chrome.router)
    app.include_router(routes_voice.router)
    app.include_router(routes_local_models.router)
    app.include_router(routes_agent.router)
    app.include_router(routes_agents.router)
    app.include_router(routes_automations.router)
    app.include_router(routes_automations.session_router)
    app.include_router(routes_integrations.router)
    app.include_router(routes_skills.router)
    app.include_router(routes_mcp.router)
    app.include_router(routes_suggestions.router)

    # Ordre : le dernier ajouté est le plus externe.
    app.add_middleware(TokenAuthMiddleware, token=token, public_paths=PUBLIC_PATHS)
    app.add_middleware(CorrelationIdMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(config.allowed_origins),
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-Correlation-ID"],
        expose_headers=["X-Correlation-ID"],
        max_age=600,
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(config.allowed_hosts))
    return app
