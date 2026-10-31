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
from ojarvis.voice.model_sets import META_MODEL_SETS, model_sets
from ojarvis.voice.spoken_en import english, spoken_english
from ojarvis.voice.stt_online import MetaTranscriber
from ojarvis.voice.tts import FallbackTTS
from ojarvis.voice.tts_meta import (
    MMS_EN_FILES,
    MMS_FILES,
    MMS_PAUSE_S,
    MMS_RATE,
    EspeakTTS,
    MmsTTS,
    MmsVoices,
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
    assert isinstance(parts["tts"].primary, MmsVoices)  # française et anglaise
    assert isinstance(parts["tts"].secondary, EspeakTTS)
    assert set(parts) == {"wake", "vad", "stt", "tts"}  # ni Groq, ni Cartesia, ni Whisper
    # Seules les voix de Meta se téléchargent.
    assert model_sets("meta") is META_MODEL_SETS
    assert [folder for folder, _files in META_MODEL_SETS.values()] == ["mms-tts-fra", "mms-tts-eng"]
    assert {"reveil", "transcription", "voix_locale"} == set(model_sets("auto"))


def test_english_sentences_are_recognized_and_spelled_out() -> None:
    assert english("The capital of Benin is Porto-Novo.")
    assert english("You have 2 tabs open.")
    assert not english("La capitale du Bénin est Porto-Novo.")
    assert not english("Tu as 3 onglets ouverts.")
    said = spoken_english("Your disk has 457 GB free, 91% used, on USB since 2026.")
    assert said == (
        "Your disk has four hundred fifty seven gigabytes free, ninety one percent used, "
        "on u s b since two thousand twenty six."
    )
    assert spoken_english("It costs $12.5.") == "It costs twelve point five dollars."


async def test_each_sentence_is_said_by_the_voice_of_its_language(tmp_path: Path) -> None:
    voices = MmsVoices(tmp_path)
    voices.french._session, voices.english._session = FakeSession(), FakeSession()
    voices.french._vocab = voices.english._vocab = {
        c: i + 1 for i, c in enumerate("abcdefghijklmnopqrstuvwxyz' ")
    }
    # La voix anglaise n'est pas encore téléchargée : la française dit tout.
    assert voices.voice_for("The capital is Porto-Novo.") is voices.french
    for name in MMS_EN_FILES:
        (tmp_path / "mms-tts-eng").mkdir(exist_ok=True)
        (tmp_path / "mms-tts-eng" / name).write_bytes(b"x")
    assert voices.voice_for("The capital is Porto-Novo.") is voices.english
    chunks = [c async for c in voices.stream(sentences("Hello, how are you?", "Je vais bien."))]
    assert len(chunks) == 2
    assert len(voices.english._session.inputs) == 2  # « Hello » et « how are you » (virgule)
    assert len(voices.french._session.inputs) == 1
