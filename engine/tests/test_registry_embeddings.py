from __future__ import annotations

import hashlib
from pathlib import Path

import httpx
import numpy as np
import pytest

from ojarvis.core.downloads import PinnedFile, sha256_of
from ojarvis.db.engine import Database
from ojarvis.db.migrate import upgrade_to_head
from ojarvis.llm.catalog import Catalog
from ojarvis.llm.providers.anthropic_provider import AnthropicProvider
from ojarvis.llm.providers.registry import (
    OllamaProvider,
    build_providers,
    discover_openrouter_free,
)
from ojarvis.memory import embeddings
from ojarvis.memory.embeddings import GEMMA_FILES, GEMMA_FOLDER, GemmaEmbedder
from ojarvis.security.secrets import MemoryBackend, SecretStore

MODEL_DIR = Path.home() / ".cache" / "o-jarvis" / "models" / GEMMA_FOLDER


def client(handler: httpx.MockTransport | None = None) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=handler)


def test_build_providers_reads_keys_from_the_store(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    upgrade_to_head(db_path)
    store = SecretStore(Database(db_path), MemoryBackend())
    providers = build_providers(store)
    assert set(providers) == {
        "deepseek",
        "anthropic",
        "openai",
        "openrouter",
        "meta",
        "baseten",
        "ollama",
    }
    assert not providers["deepseek"].is_configured()
    store.set("DEEPSEEK_API_KEY", "cle-de-test")
    assert providers["deepseek"].is_configured()
    assert isinstance(providers["anthropic"], AnthropicProvider)


async def test_ollama_refresh_and_warm_up() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "ministral-3:3b"}]})
        return httpx.Response(200, json={})

    ollama = OllamaProvider(check_every_s=60)
    assert not ollama.is_configured()
    async with client(httpx.MockTransport(handler)) as c:
        await ollama.refresh(c)
        assert ollama.is_configured()
        assert ollama.models == ["ministral-3:3b"]
        await ollama.refresh(c)  # trop tôt : pas de nouvel appel
        await ollama.warm_up(c, "ministral-3:3b")
    assert calls == ["/api/tags", "/api/generate"]


async def test_ollama_unreachable() -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refusé", request=request)

    ollama = OllamaProvider()
    async with client(httpx.MockTransport(down)) as c:
        await ollama.refresh(c, force=True)
        await ollama.warm_up(c, "x")  # l'échec est journalisé, pas levé
    assert not ollama.is_configured()
    assert not ollama.reachable


async def test_openrouter_discovery() -> None:
    payload = {
        "data": [
            {
                "id": "google/gemma-4-31b-it:free",
                "name": "Gemma",
                "context_length": 1000,
                "architecture": {"input_modalities": ["text", "image"]},
                "supported_parameters": ["tools"],
            },
            {"id": "payant/x", "name": "Payant"},
        ]
    }
    catalog = Catalog()
    async with client(httpx.MockTransport(lambda r: httpx.Response(200, json=payload))) as c:
        assert await discover_openrouter_free(catalog, c) == 1
    assert [s.model for s in catalog.all() if s.provider == "openrouter"] == [
        "google/gemma-4-31b-it:free"
    ]
    async with client(httpx.MockTransport(lambda r: httpx.Response(503))) as c:
        assert await discover_openrouter_free(catalog, c) == 0


async def test_gemma_download_checks_the_fingerprint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    content = b"contenu du fichier"
    good = hashlib.sha256(content).hexdigest()
    monkeypatch.setattr(
        embeddings,
        "GEMMA_FILES",
        {
            "model_q4.onnx": PinnedFile("https://exemple.invalid/m", good),
            "tokenizer.json": PinnedFile("https://exemple.invalid/t", "0" * 64),
        },
    )
    embedder = GemmaEmbedder(tmp_path / "modele")
    async with client(httpx.MockTransport(lambda r: httpx.Response(200, content=content))) as c:
        with pytest.raises(RuntimeError, match="empreinte"):
            await embedder.download(c)
    assert (tmp_path / "modele" / "model_q4.onnx").read_bytes() == content
    assert not (tmp_path / "modele" / "tokenizer.json").exists()
    assert not (tmp_path / "modele" / "tokenizer.json.part").exists()
    assert embedder.status == "erreur"
    assert sha256_of(tmp_path / "modele" / "model_q4.onnx") == good


async def test_gemma_without_files_and_without_network(tmp_path: Path) -> None:
    embedder = GemmaEmbedder(tmp_path / "vide")
    assert not embedder.files_present()
    with pytest.raises(RuntimeError, match="absent"):
        await embedder.ensure()
    assert (await embedder.embed([], "query")).shape == (0, 768)


@pytest.mark.skipif(
    not all((MODEL_DIR / name).is_file() for name in GEMMA_FILES),
    reason="EmbeddingGemma 2 absent de cette machine",
)
async def test_gemma_real_model_ranks_french_facts() -> None:
    embedder = GemmaEmbedder(MODEL_DIR)
    # Un long échange sur autre chose : avec e5, il arrivait en tête de presque tout.
    chatter = "J'ai réinstallé le pilote de l'imprimante, redémarré CUPS, puis réessayé. " * 30
    facts = await embedder.embed(
        [
            "Octave boit du café chaque matin.",
            "Octave habite à Lyon.",
            "Le chat s'appelle Pixel.",
            chatter + "Pour le mariage, Octave portera le costume bleu marine.",
        ],
        "passage",
    )
    assert embedder.status == "prêt"
    for question, expected in [
        ("Qu'est-ce que je bois le matin ?", 0),
        ("Dans quelle ville je vis ?", 1),
        ("Comment s'appelle mon animal ?", 2),
        ("Quelle tenue pour le mariage ?", 3),
    ]:
        query = (await embedder.embed([question], "query"))[0]
        assert int(np.argmax(facts @ query)) == expected
