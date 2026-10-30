"""La voix du profil « Tout Meta » : MMS (Meta), espeak-ng (le système), le volume du son."""

from __future__ import annotations

import shutil
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from ojarvis.settings_store import Preferences
from ojarvis.voice.audio import OUTPUT_RATE
from ojarvis.voice.service import META_MODEL_SETS, model_sets
from ojarvis.voice.stt import MetaTranscriber
from ojarvis.voice.tts import (
    MMS_FILES,
    MMS_PAUSE_S,
    MMS_RATE,
    EspeakTTS,
    FallbackTTS,
    MmsTTS,
)
from ojarvis.voice.vad import EnergyVad
from tests.voice.fakes import FakeTTS
from tests.voice.test_service import FakeEcho, make_service


class FakeSession:
    """Le modèle ONNX simulé : 100 échantillons par jeton reçu."""

    def __init__(self, *, broken: bool = False) -> None:
        self.inputs: list[list[int]] = []
        self._broken = broken

    def run(self, _outputs: Any, feeds: dict[str, Any]) -> list[Any]:
        if self._broken:
            raise RuntimeError("modèle abîmé")
        ids = feeds["input_ids"][0].tolist()
        assert feeds["attention_mask"].shape == (1, len(ids))
        self.inputs.append(ids)
        return [np.full((1, len(ids) * 100), 0.1, dtype=np.float32)]


def mms(session: FakeSession, folder: Path = Path("/inexistant")) -> MmsTTS:
    tts = MmsTTS(folder)
    tts._session = session
    tts._vocab = {c: i + 1 for i, c in enumerate("abcdefghijklmnopqrstuvwxyzéèàç' -")}
    return tts


async def sentences(*items: str) -> AsyncIterator[str]:
    for item in items:
        yield item


def test_mms_reads_letters_only_and_pauses_at_commas() -> None:
    session = FakeSession()
    tts = mms(session)
    pcm = tts._synthesize("Bonjour, il reste 2 Go.")
    # Deux morceaux (la virgule), et les chiffres arrivent écrits en lettres.
    assert len(session.inputs) == 2
    vocab = {i: c for c, i in tts._vocab.items()}
    said = ["".join(vocab[i] for i in ids if i) for ids in session.inputs]
    assert said == ["bonjour", "il reste deux gigaoctets"]
    # Comme le découpage de MMS : un jeton vide entre chaque lettre.
    assert session.inputs[0][::2] == [0] * 8
    samples = sum(len(ids) * 100 for ids in session.inputs) + int(MMS_PAUSE_S * MMS_RATE)
    assert len(pcm) // 2 == pytest.approx(samples * OUTPUT_RATE / MMS_RATE, abs=2)


async def test_a_broken_mms_hands_over_to_the_system_voice(tmp_path: Path) -> None:
    for name in MMS_FILES:
        (tmp_path / name).write_text("")  # téléchargé, mais le modèle ne marche pas
    system = FakeTTS(name="espeak")
    reasons: list[str] = []
    broken = mms(FakeSession(broken=True), tmp_path)
    voice = FallbackTTS(broken, system, on_fallback=reasons.append)
    chunks = [chunk async for chunk in voice.stream(sentences("Bonjour.", "Ça va ?"))]
    assert chunks
    assert system.sentences == ["Bonjour.", "Ça va ?"]
    assert voice.last_engine == "espeak"
    assert reasons == ["MMS : RuntimeError"]


@pytest.mark.skipif(shutil.which("espeak-ng") is None, reason="espeak-ng absent")
async def test_the_system_voice_speaks_even_a_sentence_starting_with_a_dash() -> None:
    chunks = [chunk async for chunk in EspeakTTS().stream(sentences("-3 degrés dehors."))]
    assert sum(len(c.pcm) for c in chunks) > OUTPUT_RATE  # plus d'une demi-seconde de voix
    assert {c.rate for c in chunks} == {OUTPUT_RATE}


def frames(level: float, count: int, *, tone: bool = False) -> list[Any]:
    rng = np.random.default_rng(1)
    t = np.arange(1280) / 16_000
    signal = (lambda: level * np.sin(2 * np.pi * 180 * t)) if tone else lambda: 0.0
    return [
        (signal() + rng.normal(0, 30 if tone else level, 1280)).astype(np.int16)
        for _ in range(count)
    ]


def test_speech_is_recognized_by_its_volume_above_the_room() -> None:
    vad = EnergyVad()
    assert max(vad.process(f) for f in frames(30, 20)) < 0.1  # pièce calme
    assert min(vad.process(f) for f in frames(3000, 10, tone=True)) > 0.9  # quelqu'un parle
    # Un ventilateur qui démarre devient le fond de la pièce en quelques secondes.
    fan = [vad.process(f) for f in frames(300, 80)]
    assert fan[0] > 0.5
    assert max(fan[-10:]) < 0.2
    # On parle par-dessus : toujours entendu.
    assert min(vad.process(f) for f in frames(3000, 5, tone=True)) > 0.5


async def test_the_meta_kit_holds_no_model_from_another_publisher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def no_warm_up(_self: MmsTTS) -> None:
        return None

    monkeypatch.setattr(MmsTTS, "warm_up", no_warm_up)
    service, _bus, _chat = make_service(
        {"value": Preferences()}, echo=FakeEcho(available=False), sources=[]
    )
    parts = await service._meta_parts()
    assert parts["wake"] is None  # le raccourci, le bouton micro ou « /voix »
    assert isinstance(parts["vad"], EnergyVad)
    assert isinstance(parts["stt"], MetaTranscriber)
    assert isinstance(parts["tts"].primary, MmsTTS)
    assert isinstance(parts["tts"].secondary, EspeakTTS)
    assert set(parts) == {"wake", "vad", "stt", "tts"}  # ni Groq, ni Cartesia, ni Whisper
    # Seule la voix de Meta se télécharge.
    assert model_sets("meta") is META_MODEL_SETS
    assert [folder for folder, _files in META_MODEL_SETS.values()] == ["mms-tts-fra"]
    assert {"reveil", "transcription", "voix_locale"} == set(model_sets("auto"))
