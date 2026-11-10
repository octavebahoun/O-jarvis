"""Muse Voice Transcribe, la transcription de Meta (profil « Tout Meta »), construite pendant le
hackathon Meta (O-jarvis Shell) : la phrase dite, envoyée seulement après le raccourci, le
bouton micro ou « /voix ».
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

from ojarvis.voice.audio import RATE, Pcm
from ojarvis.voice.stt import SttError, Transcript, wav_bytes
from ojarvis.voice.stt_online import _retry_after

META_STT_URL = "https://api.meta.ai/v1/asr/transcribe"

META_STT_MODEL = "muse-voice-transcribe-1.0"

#: Indices, pas des contraintes : sans eux, « Jarvis » devenait « Jarvi » à l'essai.
META_STT_SETTINGS: dict[str, Any] = {
    "mode": "PUSH_TO_TALK",
    "model": META_STT_MODEL,
    "audioEncoding": "WAV",
    "languageBias": ["French"],
    "keywords": ["Jarvis"],
}


class MetaTranscriber:
    """Muse Voice Transcribe (API Meta), profil « Tout Meta » : la phrase entière, à la pause.

    Essai du 26/10/2026 : 2,3 s pour une phrase de 4 s (Groq est plus rapide). Meta ne donne
    pas de score de confiance : la phrase est tenue pour fiable.
    """

    name = "meta"

    def __init__(
        self, api_key: Callable[[], str | None], http: Any, *, timeout_s: float = 6.0
    ) -> None:
        self._api_key = api_key
        self._http = http
        self._timeout_s = timeout_s
        #: Après un « 429 », Meta est laissé de côté le temps qu'il indique.
        self._blocked_until = 0.0

    @property
    def available(self) -> bool:
        return bool(self._api_key()) and time.monotonic() >= self._blocked_until

    async def transcribe(self, audio: Pcm) -> Transcript:
        import httpx  # noqa: PLC0415

        key = self._api_key()
        if not key:
            raise SttError("clé Meta absente")
        start = time.perf_counter()
        try:
            response = await self._http.post(
                META_STT_URL,
                headers={"Authorization": f"Bearer {key}"},
                files={
                    "request": (None, json.dumps(META_STT_SETTINGS), "application/json"),
                    "audio": ("phrase.wav", wav_bytes(audio), "audio/wav"),
                },
                timeout=self._timeout_s,
            )
        except httpx.HTTPError as exc:
            raise SttError(f"Meta injoignable : {type(exc).__name__}") from exc
        if response.status_code == 429:
            wait = _retry_after(response.headers.get("retry-after"), default=20.0)
            self._blocked_until = time.monotonic() + wait
            raise SttError(f"Meta limite les requêtes (HTTP 429), pause de {wait:.0f} s")
        if response.status_code != 200:
            raise SttError(f"Meta a refusé la transcription (HTTP {response.status_code})")
        return Transcript(
            text=str(response.json().get("transcript", "")).strip(),
            audio_s=round(len(audio) / RATE, 2),
            duration_ms=round((time.perf_counter() - start) * 1000, 1),
        )
