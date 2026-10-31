"""Transcription par Muse Voice Transcribe (API Meta), avec un serveur HTTP simulé."""

from __future__ import annotations

import json

import httpx
import numpy as np
import pytest

from ojarvis.voice.stt import SttError
from ojarvis.voice.stt_online import META_STT_URL, MetaTranscriber


def answer(request: httpx.Request) -> httpx.Response:
    body = request.content.decode("latin-1")
    assert request.url == httpx.URL(META_STT_URL)
    assert request.headers["Authorization"] == "Bearer meta-test"
    # Deux parties : les réglages en JSON, puis le fichier WAV.
    assert 'name="request"' in body
    assert 'name="audio"' in body
    assert "RIFF" in body
    settings = json.loads(body.split("application/json\r\n\r\n", 1)[1].split("\r\n--", 1)[0])
    assert settings["model"] == "muse-voice-transcribe-1.0"
    assert settings["languageBias"] == ["French"]
    assert "Jarvis" in settings["keywords"]
    return httpx.Response(
        200,
        json={
            "sessionId": "s1",
            "transcript": " Pourquoi mon disque est plein ?",
            "audioDurationMs": 1000,
            "turns": [],
        },
    )


async def test_the_sentence_goes_to_meta_in_french_and_comes_back() -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as http:
        stt = MetaTranscriber(lambda: "meta-test", http)
        transcript = await stt.transcribe(np.zeros(16_000, dtype=np.int16))
    assert transcript.text == "Pourquoi mon disque est plein ?"
    assert transcript.audio_s == 1.0
    assert transcript.reliable  # Meta ne donne pas de score : la phrase est retenue


@pytest.mark.parametrize("status", [400, 401, 413, 500])
async def test_a_refusal_is_an_error(status: int) -> None:
    transport = httpx.MockTransport(lambda _r: httpx.Response(status, json={}))
    async with httpx.AsyncClient(transport=transport) as http:
        with pytest.raises(SttError, match=str(status)):
            await MetaTranscriber(lambda: "meta-test", http).transcribe(np.zeros(800, np.int16))


async def test_an_unreachable_service_is_an_error() -> None:
    def down(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("réseau coupé")

    async with httpx.AsyncClient(transport=httpx.MockTransport(down)) as http:
        with pytest.raises(SttError, match="injoignable"):
            await MetaTranscriber(lambda: "meta-test", http).transcribe(np.zeros(800, np.int16))


async def test_without_key_nothing_is_sent() -> None:
    sent: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(record)) as http:
        stt = MetaTranscriber(lambda: None, http)
        assert not stt.available
        with pytest.raises(SttError, match="absente"):
            await stt.transcribe(np.zeros(800, np.int16))
    assert sent == []


async def test_a_rate_limit_sets_meta_aside_for_the_time_it_asks() -> None:
    transport = httpx.MockTransport(
        lambda _r: httpx.Response(429, headers={"retry-after": "30"}, json={})
    )
    async with httpx.AsyncClient(transport=transport) as http:
        stt = MetaTranscriber(lambda: "meta-test", http)
        with pytest.raises(SttError, match="30 s"):
            await stt.transcribe(np.zeros(800, np.int16))
    assert not stt.available  # pendant 30 s, les phrases passent par le relais
