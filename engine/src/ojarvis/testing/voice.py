"""Composants simulés du pipeline vocal, et un petit langage pour fabriquer de l'audio.

L'audio de test est symbolique : des trames constantes pour « Hey Jarvis », une sinusoïde
forte pour la parole, un léger bruit pour le silence. Les faux détecteurs lisent ces
signaux ; la vraie détection est testée à part, sur de vrais modèles.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import numpy as np

from ojarvis.chat.service import ChatEvent
from ojarvis.events.bus import EventBus
from ojarvis.settings_store import Preferences
from ojarvis.voice.audio import (
    FRAME_SAMPLES,
    RATE,
    FileSource,
    Frame,
    MemorySink,
    Pcm,
    VirtualClock,
)
from ojarvis.voice.service import VoiceService
from ojarvis.voice.stt import SttError, Transcript
from ojarvis.voice.tts import AudioChunk

WAKE_VALUE = 3000


def build_audio(*segments: tuple[str, float]) -> Pcm:
    """("silence", 1.0), ("wake", 0.8), ("speech", 1.5)… → PCM 16 kHz."""
    rng = np.random.default_rng(3)
    parts: list[Pcm] = []
    for kind, seconds in segments:
        count = round(seconds * RATE / FRAME_SAMPLES) * FRAME_SAMPLES
        if kind == "silence":
            parts.append(rng.normal(0, 10, count).astype(np.int16))
        elif kind == "wake":
            parts.append(np.full(count, WAKE_VALUE, dtype=np.int16))
        elif kind == "speech":
            t = np.arange(count) / RATE
            parts.append((8000 * np.sin(2 * np.pi * 220 * t)).astype(np.int16))
        else:
            raise ValueError(kind)
    return np.concatenate(parts)


class FakeWake:
    """Score fort sur les trames « Hey Jarvis » (constantes)."""

    def __init__(self, score: float = 0.95) -> None:
        self.wake_score = score
        self.resets = 0

    def score(self, pcm: Pcm) -> float:
        return self.wake_score if bool(np.all(pcm == WAKE_VALUE)) else 0.01

    def reset(self) -> None:
        self.resets += 1


class FakeVad:
    """Parole = signal fort (le marqueur de réveil compte comme de la parole)."""

    def process(self, pcm: Pcm) -> float:
        rms = float(np.sqrt(np.mean(np.square(pcm.astype(np.float32)))))
        return 0.95 if rms > 1000 else 0.02

    def reset(self) -> None:
        return None


@dataclass
class FakeSTT:
    """Renvoie les textes prévus, dans l'ordre (le dernier se répète)."""

    texts: list[str] = field(default_factory=lambda: ["Hey Jarvis, quelle heure est-il ?"])
    calls: list[float] = field(default_factory=list)
    logprob: float = -0.2

    async def transcribe(self, audio: Pcm) -> Transcript:
        seconds = len(audio) / RATE
        self.calls.append(seconds)
        text = self.texts[min(len(self.calls) - 1, len(self.texts) - 1)]
        await asyncio.sleep(0)
        return Transcript(text=text, audio_s=seconds, duration_ms=12.0, avg_logprob=self.logprob)


@dataclass
class FakeTTS:
    """Une seconde d'audio par phrase (silence numérique), au fil de l'eau."""

    seconds_per_sentence: float = 1.0
    name: str = "faux"
    sentences: list[str] = field(default_factory=list)
    warmups: int = 0

    @property
    def available(self) -> bool:
        return True

    async def warm_up(self) -> None:
        self.warmups += 1

    async def close(self) -> None:
        return None

    async def stream(self, sentences: AsyncIterator[str]) -> AsyncGenerator[AudioChunk]:
        async for sentence in sentences:
            self.sentences.append(sentence)
            samples = int(24_000 * self.seconds_per_sentence)
            yield AudioChunk(np.full(samples, 500, dtype=np.int16).tobytes(), 24_000)


@dataclass
class FakeChat:
    """Imite `ChatService.respond` : réponse prévue, erreur, ou attente sans fin."""

    reply: str = "Il est dix heures. Autre chose ?"
    error: bool = False
    hang: bool = False
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def respond(
        self,
        *,
        text: str,
        conversation_id: str | None = None,
        channel: str = "app",
        learn: bool = True,
    ) -> AsyncIterator[ChatEvent]:
        self.calls.append(
            {"text": text, "conversation_id": conversation_id, "channel": channel, "learn": learn}
        )
        yield ChatEvent("start", {"conversation_id": conversation_id or "conv-1"})
        if self.hang:
            await asyncio.Event().wait()
        if self.error:
            yield ChatEvent("error", {"message": "Le fournisseur ne répond pas.", "partial": False})
            return
        for word in self.reply.split(" "):
            yield ChatEvent("delta", {"text": word + " "})
        yield ChatEvent("done", {"conversation_id": "conv-1", "first_token_ms": 42.0})


class FakeTracer:
    def __init__(self) -> None:
        self.spans: list[str] = []
        self.meta: dict[str, Any] = {}

    @contextlib.asynccontextmanager
    async def trace(self, name: str, **_: Any) -> AsyncIterator[Any]:
        tracer = self

        class Handle:
            id = "trace-1"

            def set(self, **meta: Any) -> None:
                tracer.meta.update(meta)

            def fail(self, message: str) -> None:
                tracer.meta["error"] = message

        yield Handle()

    @contextlib.asynccontextmanager
    async def span(self, name: str, **_: Any) -> AsyncIterator[Any]:
        self.spans.append(name)

        class Span:
            def set(self, **_: Any) -> None:
                return None

        yield Span()

    async def add_span(self, name: str, **_: Any) -> None:
        self.spans.append(name)


async def wait_until(condition: Callable[[], bool], limit: float = 5.0) -> None:
    """Attend qu'une condition, changée par les tâches du pipeline, devienne vraie."""
    async with asyncio.timeout(limit):
        while not condition():  # noqa: ASYNC110 — l'état vient d'autres tâches
            await asyncio.sleep(0.005)


@dataclass
class FakeSttStream:
    owner: FakeStreamingSTT
    samples: int = 0
    flushes: int = 0
    cancelled: bool = False

    def send(self, pcm: Pcm) -> None:
        self.samples += len(pcm)

    def flush(self) -> None:
        self.flushes += 1

    async def finish(self, timeout_s: float = 3.0) -> Transcript:
        del timeout_s
        await asyncio.sleep(0)
        if self.owner.fail:
            raise SttError("panne du service")
        return Transcript(text=self.owner.text, audio_s=self.samples / RATE, duration_ms=5.0)

    async def cancel(self) -> None:
        self.cancelled = True


@dataclass
class FakeStreamingSTT:
    """Transcription en ligne simulée : compte ce qui lui est envoyé."""

    text: str = "Hey Jarvis, quelle heure est-il ?"
    fail: bool = False
    name: str = "cartesia"
    streams: list[FakeSttStream] = field(default_factory=list)

    @property
    def available(self) -> bool:
        return True

    def open(self) -> FakeSttStream:
        self.streams.append(FakeSttStream(self))
        return self.streams[-1]


@dataclass
class FakeCloudSTT:
    """Transcription par phrase simulée (Groq)."""

    text: str = "Hey Jarvis, quelle heure est-il ?"
    fail: bool = False
    has_key: bool = True
    name: str = "groq"
    calls: list[float] = field(default_factory=list)

    @property
    def available(self) -> bool:
        return self.has_key

    async def transcribe(self, audio: Pcm) -> Transcript:
        self.calls.append(len(audio) / RATE)
        await asyncio.sleep(0)
        if self.fail:
            raise SttError("Groq injoignable")
        return Transcript(text=self.text, audio_s=len(audio) / RATE, duration_ms=30.0)


class FakeEcho:
    def __init__(self, available: bool) -> None:
        self.available = available
        self.loads: list[tuple[str | None, str | None]] = []
        self.unloads = 0

    def load(self, source: str | None, sink: str | None) -> bool:
        self.loads.append((source, sink))
        return self.available

    def unload(self) -> None:
        self.unloads += 1


class BrokenSource:
    broken = True

    async def frames(self) -> AsyncIterator[Frame]:
        if self.broken:
            raise RuntimeError("PortAudio : micro débranché")
        yield Frame(np.zeros(1280, dtype=np.int16), 0.0)  # pragma: no cover

    async def close(self) -> None:
        return None


def make_service(
    prefs: dict[str, Preferences],
    *,
    echo: FakeEcho,
    sources: list[str | None],
    broken: bool = False,
) -> tuple[VoiceService, EventBus, FakeChat]:
    clock = VirtualClock()
    bus = EventBus()
    chat = FakeChat()
    audio = build_audio(("silence", 0.5), ("wake", 0.8), ("speech", 1.0))

    def source(device: str | None) -> Any:
        sources.append(device)
        return BrokenSource() if broken else FileSource(audio, clock=clock)

    service = VoiceService(
        models_dir=Path("/inexistant"),
        http=httpx.AsyncClient(),
        bus=bus,
        tracer=FakeTracer(),  # type: ignore[arg-type]
        chat=chat,  # type: ignore[arg-type]
        secrets=None,  # type: ignore[arg-type]
        preferences=lambda: prefs["value"],
        parts={"wake": FakeWake(), "vad": FakeVad(), "stt": FakeSTT(), "tts": FakeTTS()},
        source_factory=source,
        sink_factory=lambda _device: MemorySink(clock=clock),
        echo=echo,  # type: ignore[arg-type]
        clock=clock,
    )
    return service, bus, chat
