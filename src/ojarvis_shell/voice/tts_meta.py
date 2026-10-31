"""Les voix du profil « Tout Meta » : MMS, la voix de Meta (française et anglaise), lancée ici
sur le processeur, et en secours espeak-ng, la voix du système (pas un modèle d'IA).
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from pathlib import Path
from typing import Any

import anyio.to_thread
import numpy as np

from ojarvis.core.downloads import PinnedFile
from ojarvis.voice.audio import OUTPUT_RATE, resample
from ojarvis.voice.spoken import spoken_french
from ojarvis.voice.spoken_en import english, spoken_english
from ojarvis.voice.tts import AudioChunk, TTSError

#: MMS TTS français de Meta (facebook/mms-tts-fra), exporté en ONNX : 114 Mo.
MMS_REVISION = "c7aa283bf5b599d3c804e4accab07f6394a2b4ae"

MMS_BASE = f"https://huggingface.co/Xenova/mms-tts-fra/resolve/{MMS_REVISION}"

MMS_FILES: dict[str, PinnedFile] = {
    "model.onnx": PinnedFile(
        f"{MMS_BASE}/onnx/model.onnx",
        "429b27e1cc954d7da4b88e6c73933ad8c3dc033f941e9a04fd2091c1e7ceba41",
        114_263_414,
    ),
    "vocab.json": PinnedFile(
        f"{MMS_BASE}/vocab.json",
        "d11c7389cb4eec1237a964f18ed1973a47c8b6db6ad0726c065dd2ade6a9c5eb",
        493,
    ),
}

#: La voix anglaise de Meta (facebook/mms-tts-eng), même export : Jarvis répond dans la
#: langue de la question.
MMS_EN_REVISION = "3f8955a2adbd6487ce57420620d4916de22b9dac"

MMS_EN_BASE = f"https://huggingface.co/Xenova/mms-tts-eng/resolve/{MMS_EN_REVISION}"

MMS_EN_FILES: dict[str, PinnedFile] = {
    "model.onnx": PinnedFile(
        f"{MMS_EN_BASE}/onnx/model.onnx",
        "cbfd1e634a7bd1fce6690176ab3bfdb2aeb3b3ed756591f85f654a6669cadd3d",
        114_258_806,
    ),
    "vocab.json": PinnedFile(
        f"{MMS_EN_BASE}/vocab.json",
        "304f7e71c40d601f9b89d81904609512eebe96b2a941f0a251fc0c9f397bcc45",
        413,
    ),
}

MMS_RATE = 16_000

#: MMS ne lit pas la ponctuation : un court silence sépare les morceaux d'une phrase.
MMS_PAUSE_S = 0.18

_PIECES = re.compile(r"\s*[,;:()\u2013\u2014]\s*")


class MmsTTS:
    """La voix de Meta (MMS, français), phrase par phrase, dans un fil séparé.

    Essai du 30/10/2026 sur un i5-4200M : 4,4 s de calcul pour 8,9 s de voix, la version
    complète allant plus vite que la version quantifiée.
    """

    name = "mms"

    def __init__(
        self,
        folder: Path,
        *,
        files: dict[str, PinnedFile] = MMS_FILES,
        normalize: Callable[[str], str] = spoken_french,
        greeting: str = "Bonjour.",
    ) -> None:
        self._folder = folder
        self._files = files
        self._normalize = normalize
        self._greeting = greeting
        self._session: Any = None
        self._vocab: dict[str, int] = {}
        self._lock = asyncio.Lock()

    @property
    def available(self) -> bool:
        return all((self._folder / name).is_file() for name in self._files)

    def _load(self) -> Any:
        if self._session is None:
            import onnxruntime as ort  # noqa: PLC0415

            options = ort.SessionOptions()
            options.log_severity_level = 4
            self._vocab = json.loads((self._folder / "vocab.json").read_text(encoding="utf-8"))
            self._session = ort.InferenceSession(
                str(self._folder / "model.onnx"),
                sess_options=options,
                providers=["CPUExecutionProvider"],
            )
        return self._session

    def _ids(self, text: str) -> list[int]:
        # Comme le découpage de MMS : un jeton vide (0) entre chaque lettre connue.
        ids = [0]
        for char in text.lower():
            if char in self._vocab:
                ids += [self._vocab[char], 0]
        return ids

    def _synthesize(self, sentence: str) -> bytes:
        session = self._load()
        pause = np.zeros(int(MMS_PAUSE_S * MMS_RATE), dtype=np.float32)
        audio: list[np.ndarray[Any, Any]] = []
        for piece in _PIECES.split(self._normalize(sentence)):
            ids = self._ids(piece)
            if len(ids) < 3:  # rien de prononçable
                continue
            feeds = {
                "input_ids": np.array([ids], dtype=np.int64),
                "attention_mask": np.ones((1, len(ids)), dtype=np.int64),
            }
            wave = np.asarray(session.run(None, feeds)[0], dtype=np.float32).reshape(-1)
            audio += [pause, wave] if audio else [wave]
        if not audio:
            return b""
        pcm = (np.clip(np.concatenate(audio), -1.0, 1.0) * 32767).astype(np.int16)
        return resample(pcm, MMS_RATE, OUTPUT_RATE).tobytes()

    def _warm(self) -> None:
        if self._session is None:
            self._load()
            # La première phrase coûte 2 s de plus (essai du 30/10) : on la paie au démarrage.
            self._synthesize(self._greeting)

    async def warm_up(self) -> None:
        async with self._lock:
            await anyio.to_thread.run_sync(self._warm)

    async def close(self) -> None:
        return None

    async def say(self, sentence: str) -> bytes:
        """Une phrase, en PCM 16 bits à la fréquence de sortie (vide : rien à dire)."""
        async with self._lock:
            return await anyio.to_thread.run_sync(self._synthesize, sentence)

    async def stream(self, sentences: AsyncIterator[str]) -> AsyncGenerator[AudioChunk]:
        spoken = False
        async for sentence in sentences:
            try:
                pcm = await self.say(sentence)
            except Exception as exc:
                raise TTSError(f"MMS : {type(exc).__name__}", spoken=spoken) from exc
            if pcm:
                spoken = True
                yield AudioChunk(pcm, OUTPUT_RATE)


class MmsVoices:
    """Les voix de Meta en français et en anglais : chaque phrase est dite par celle de sa
    langue. Tant que la voix anglaise n'est pas téléchargée, la française dit tout ; elle ne
    se charge qu'à la première phrase anglaise (environ 150 Mo de mémoire)."""

    name = "mms"

    def __init__(self, models: Path) -> None:
        self.french = MmsTTS(models / "mms-tts-fra")
        self.english = MmsTTS(
            models / "mms-tts-eng", files=MMS_EN_FILES, normalize=spoken_english, greeting="Hello."
        )

    @property
    def available(self) -> bool:
        return self.french.available

    async def warm_up(self) -> None:
        await self.french.warm_up()

    async def close(self) -> None:
        return None

    def voice_for(self, sentence: str) -> MmsTTS:
        return self.english if english(sentence) and self.english.available else self.french

    async def stream(self, sentences: AsyncIterator[str]) -> AsyncGenerator[AudioChunk]:
        spoken = False
        async for sentence in sentences:
            try:
                pcm = await self.voice_for(sentence).say(sentence)
            except Exception as exc:
                raise TTSError(f"MMS : {type(exc).__name__}", spoken=spoken) from exc
            if pcm:
                spoken = True
                yield AudioChunk(pcm, OUTPUT_RATE)


class EspeakTTS:
    """La voix du système (espeak-ng, installé avec Ubuntu) : pas un modèle d'IA."""

    name = "espeak"

    def __init__(self, *, voice: str = "fr", words_per_minute: int = 165) -> None:
        self._voice = voice
        self._speed = words_per_minute

    @property
    def available(self) -> bool:
        return shutil.which("espeak-ng") is not None

    async def warm_up(self) -> None:
        return None

    async def close(self) -> None:
        return None

    def _synthesize(self, sentence: str) -> bytes:
        binary = shutil.which("espeak-ng")
        if binary is None:
            raise TTSError("espeak-ng absent")
        # Le texte passe par l'entrée standard : une phrase qui commence par « - » n'est pas
        # prise pour une option.
        done = subprocess.run(  # noqa: S603 — binaire système, arguments fixes
            [binary, "-v", self._voice, "-s", str(self._speed), "--stdout"],
            input=sentence.encode(),
            capture_output=True,
            timeout=30,
            check=False,
        )
        wav = done.stdout
        if done.returncode != 0 or len(wav) <= 44:
            raise TTSError(f"espeak-ng a échoué (code {done.returncode})")
        rate = int.from_bytes(wav[24:28], "little")  # en-tête WAV, tailles laissées vides
        pcm = np.frombuffer(wav[44 : 44 + (len(wav) - 44) // 2 * 2], dtype=np.int16)
        return resample(pcm, rate, OUTPUT_RATE).tobytes()

    async def stream(self, sentences: AsyncIterator[str]) -> AsyncGenerator[AudioChunk]:
        async for sentence in sentences:
            pcm = await anyio.to_thread.run_sync(self._synthesize, sentence)
            if pcm:
                yield AudioChunk(pcm, OUTPUT_RATE)
