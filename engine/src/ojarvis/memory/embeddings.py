"""Embeddings locaux : EmbeddingGemma 2 (sa partie texte, quantifiée en q4), exécuté par ONNX
Runtime sur le CPU.

Le modèle (~207 Mo) est téléchargé une fois, vérifié par empreinte SHA-256, puis gardé
en cache. Aucun texte ne quitte la machine pour être vectorisé.

Il a remplacé multilingual-e5-small le 10/11/2026, après un banc d'essai en français sur un
i5 de 2015 (21 questions sur 20 souvenirs) : 18 bonnes réponses en tête au lieu de 12, en
110 ms par question. e5 lisait 256 jetons au plus et notait tout entre 0,7 et 1 ; un long
texte y arrivait en tête de presque toutes les recherches.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import threading
import unicodedata
from collections import OrderedDict
from pathlib import Path
from typing import Any, Literal, Protocol

import anyio.to_thread
import httpx
import numpy as np
import numpy.typing as npt

from ojarvis.core.downloads import PinnedFile, fetch_pinned, files_present
from ojarvis.core.logs import get_logger

log = get_logger(__name__)

Kind = Literal["query", "passage"]
Vectors = npt.NDArray[np.float32]

#: Révision figée du dépôt : les empreintes ci-dessous sont celles de ces fichiers-là.
HF_BASE = (
    "https://huggingface.co/onnx-community/embeddinggemma-2-ONNX/resolve/"
    "daa72c51243991dfcaf9f9137d2c573d8f7790c0"
)
GEMMA_FOLDER = "embeddinggemma-2-q4"
GEMMA_FILES: dict[str, PinnedFile] = {
    "model_q4.onnx": PinnedFile(
        f"{HF_BASE}/onnx/model_q4.onnx",
        "f9eeba97acddf139b8ee2ddf04bc30dceafa88de93fadf74d7644e0d61a477a9",
        490_742,
    ),
    # Les poids, à côté du graphe et sous ce nom exact : ONNX Runtime les y cherche.
    "model_q4.onnx_data": PinnedFile(
        f"{HF_BASE}/onnx/model_q4.onnx_data",
        "c3975f2d1ab7a1878ae31a7d7a9b7804a827aff3800b60dfceafce21cac3df49",
        174_028_800,
    ),
    "tokenizer.json": PinnedFile(
        f"{HF_BASE}/tokenizer.json",
        "4d777ef5bdc1aa36227abdfb77c3e49e7b9c892d16e1b6bda41c393504828be4",
        32_170_510,
    ),
}

#: Les préfixes de tâche appris par le modèle (sa fiche) : une question, un texte à retrouver.
PREFIXES: dict[Kind, str] = {
    "query": "task: search result | query: ",
    "passage": "title: none | text: ",
}
#: Le modèle lit 8 192 jetons, mais le CPU paie chacun : au-delà, la suite est ignorée.
MAX_TOKENS = 1024
_BATCH = 8
#: Les derniers textes lus et leurs vecteurs, gardés pour ne pas relire un même message.
_RECENT = 256
#: Seul le texte est vectorisé : les entrées image, vidéo et son restent vides.
_NO_MEDIA = np.zeros((0, 512), dtype=np.float32)


class Embedder(Protocol):
    model_id: str
    dim: int

    @property
    def status(self) -> str: ...

    async def embed(self, texts: list[str], kind: Kind) -> Vectors: ...


def _normalize(vectors: npt.NDArray[np.float32]) -> Vectors:
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    result: Vectors = (vectors / norms).astype(np.float32)
    return result


class GemmaEmbedder:
    model_id = "eg2"
    dim = 768

    def __init__(self, folder: Path, *, threads: int = 2) -> None:
        self.folder = folder
        self._threads = threads
        self._session: Any = None
        self._tokenizer: Any = None
        self._ready = asyncio.Event()
        self._load_lock = asyncio.Lock()
        self._run_lock = threading.Lock()
        self._status = "absent"
        self._recent: OrderedDict[tuple[Kind, str], npt.NDArray[np.float32]] = OrderedDict()

    @property
    def status(self) -> str:
        return self._status

    def files_present(self) -> bool:
        return files_present(self.folder, GEMMA_FILES)

    async def download(self, client: httpx.AsyncClient) -> None:
        self._status = "téléchargement"
        try:
            await fetch_pinned(client, self.folder, GEMMA_FILES)
        except Exception:
            self._status = "erreur"
            raise

    async def ensure(self, client: httpx.AsyncClient | None = None) -> None:
        if self._ready.is_set():
            return
        async with self._load_lock:
            if self._ready.is_set():
                return
            if not self.files_present():
                if client is None:
                    raise RuntimeError("modèle d'embeddings absent")
                await self.download(client)
            await anyio.to_thread.run_sync(self._load)
            self._status = "prêt"
            self._ready.set()

    def _load(self) -> None:
        import onnxruntime as ort  # noqa: PLC0415
        from tokenizers import Tokenizer  # noqa: PLC0415

        tokenizer = Tokenizer.from_file(str(self.folder / "tokenizer.json"))
        tokenizer.enable_truncation(max_length=MAX_TOKENS)
        tokenizer.enable_padding(pad_id=0, pad_token="<pad>")  # noqa: S106
        options = ort.SessionOptions()
        options.intra_op_num_threads = self._threads
        options.inter_op_num_threads = 1
        self._session = ort.InferenceSession(
            str(self.folder / "model_q4.onnx"), options, providers=["CPUExecutionProvider"]
        )
        self._tokenizer = tokenizer

    def _run(self, texts: list[str]) -> Vectors:
        with self._run_lock:
            encodings = self._tokenizer.encode_batch(texts)
            feeds = {
                "input_ids": np.array([e.ids for e in encodings], dtype=np.int64),
                "attention_mask": np.array([e.attention_mask for e in encodings], dtype=np.int64),
                "image_features": _NO_MEDIA,
                "video_features": _NO_MEDIA,
                "audio_features": _NO_MEDIA,
            }
            pooled = self._session.run(["sentence_embedding"], feeds)[0]
        return _normalize(np.asarray(pooled, dtype=np.float32))

    async def embed(self, texts: list[str], kind: Kind) -> Vectors:
        """Les vecteurs des textes. Un texte déjà lu récemment n'est pas relu : un même message
        sert à la mémoire, à la bibliothèque, aux demandes répétées et au Système 1."""
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        found: dict[str, npt.NDArray[np.float32]] = {}
        for text in texts:
            if (kind, text) in self._recent:
                self._recent.move_to_end((kind, text))
                found[text] = self._recent[(kind, text)]
        missing = [text for text in dict.fromkeys(texts) if text not in found]
        if missing:
            await self.ensure()
            prefixed = [PREFIXES[kind] + text for text in missing]
            parts = [
                await anyio.to_thread.run_sync(self._run, prefixed[i : i + _BATCH])
                for i in range(0, len(prefixed), _BATCH)
            ]
            for text, vector in zip(missing, np.concatenate(parts, axis=0), strict=True):
                found[text] = vector
                self._recent[(kind, text)] = vector
            while len(self._recent) > _RECENT:
                self._recent.popitem(last=False)
        return np.stack([found[text] for text in texts])


_WORD = re.compile(r"\w+", re.UNICODE)


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


class HashEmbedder:
    """Embeddings déterministes par hachage de mots : les tests, sans modèle à télécharger."""

    model_id = "hash"

    def __init__(self, dim: int = 384) -> None:
        self.dim = dim

    @property
    def status(self) -> str:
        return "prêt"

    async def embed(self, texts: list[str], kind: Kind) -> Vectors:
        del kind
        rows = np.zeros((len(texts), self.dim), dtype=np.float32)
        for row, text in enumerate(texts):
            for word in _WORD.findall(_fold(text)):
                if len(word) < 3:
                    continue
                digest = hashlib.blake2b(word.encode(), digest_size=8).digest()
                bucket = int.from_bytes(digest[:4], "little") % self.dim
                sign = 1.0 if digest[4] % 2 else -1.0
                rows[row, bucket] += sign
        return _normalize(rows)
