"""Service vocal et routes : sans audio réel dans les tests, le service reste éteint proprement."""

from __future__ import annotations

import json
import shutil
import subprocess
import types
from typing import Any

import anyio
import httpx
import pytest
from fastapi.testclient import TestClient

from ojarvis.services import Services
from ojarvis.settings_store import Preferences
from ojarvis.testing.fixtures import Router
from ojarvis.testing.voice import FakeEcho, make_service
from ojarvis.voice.devices import EchoCancel, list_devices
from ojarvis.voice.model_sets import SENSITIVITY
from tests.voice.fakes import (
    wait_until,
)


def test_status_without_audio_is_off_with_a_reason(client: TestClient) -> None:
    status = client.get("/v1/voice/status").json()
    assert status["state"] == "off"
    assert status["enabled"] is True  # réglage par défaut : la voix est voulue…
    assert status["error"] == "Audio désactivé pour ce lancement."  # …mais pas d'audio ici


def test_listen_needs_a_running_pipeline(client: TestClient) -> None:
    response = client.post("/v1/voice/listen")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "voice_off"
    assert client.post("/v1/voice/stop").status_code == 204


def test_voice_settings_are_validated(client: TestClient) -> None:
    ok = client.patch(
        "/v1/settings",
        json={"wake_sensitivity": "haute", "voice_engine": "piper", "voice_follow_up": False},
    )
    assert ok.status_code == 200
    assert ok.json()["wake_sensitivity"] == "haute"
    bad = client.patch("/v1/settings", json={"voice_name": "pas une voix ; rm -rf"})
    assert bad.status_code == 422
    sneaky = {"voice_input_device": "alsa_input.pci aec_args=analog_gain_control=1"}
    assert client.patch("/v1/settings", json=sneaky).status_code == 422
    usb = {"voice_input_device": "alsa_input.usb-Logitech_USB_Headset-00.mono-fallback"}
    assert client.patch("/v1/settings", json=usb).status_code == 200
    assert client.patch("/v1/settings", json={"wake_sensitivity": "extrême"}).status_code == 422


def test_turning_voice_off_is_reflected(client: TestClient, services: Services) -> None:
    client.patch("/v1/settings", json={"voice_enabled": False})
    client.portal.call(services.voice.apply, services.preferences)  # type: ignore[union-attr]
    status = client.get("/v1/voice/status").json()
    assert status["enabled"] is False
    assert status["state"] == "off"


def test_cartesia_voices_are_listed_without_leaking_the_key(
    client: TestClient, upstream: dict[str, Router]
) -> None:
    seen: list[httpx.Request] = []

    def cartesia(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": "abc-123", "name": "Étienne", "language": "fr", "gender": "masculine"},
                    {"id": "def-456", "name": "Katie", "language": "en"},
                ]
            },
        )

    upstream["api.cartesia.ai"] = cartesia
    client.put("/v1/secrets/CARTESIA_API_KEY", json={"value": "sk_car_cle_de_test_123456"})
    voices = client.get("/v1/voice/voices").json()
    assert [v["name"] for v in voices] == ["Étienne"]
    assert seen[0].headers["X-API-Key"] == "sk_car_cle_de_test_123456"
    assert "sk_car" not in str(seen[0].url)


def test_voices_without_key_is_an_empty_list(client: TestClient) -> None:
    assert client.get("/v1/voice/voices").json() == []


def test_sensitivity_levels_are_ordered() -> None:
    assert SENSITIVITY["haute"][0] < SENSITIVITY["normale"][0] < SENSITIVITY["basse"][0]
    assert Preferences().wake_sensitivity == "normale"


def test_echo_cancel_without_pactl_is_a_soft_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    echo = EchoCancel()
    assert echo.load(None, None) is False
    echo.unload()  # sans module chargé : rien à faire, pas d'erreur


# ---------------------------------------------------------------- cycle de vie (sans vrai micro)


async def test_service_runs_a_turn_and_follows_settings() -> None:
    prefs = {"value": Preferences(voice_follow_up=False)}
    echo = FakeEcho(available=True)
    sources: list[str | None] = []
    service, _bus, chat = make_service(prefs, echo=echo, sources=sources)
    await service.start()
    assert service.status.echo_cancel is True
    assert sources == ["ojarvis_micro"]  # micro avec annulation d'écho
    await wait_until(lambda: service.status.last_latency_ms is not None)
    assert chat.calls[0]["channel"] == "voice"
    assert service.snapshot()["tts_engine"] == "faux"

    # Un seuil change : pas de redémarrage, le pipeline garde son micro.
    prefs["value"] = prefs["value"].model_copy(update={"wake_sensitivity": "haute"})
    await service.apply(prefs["value"])
    assert len(sources) == 1
    assert service._pipeline is not None
    assert service._pipeline.config.wake_threshold == SENSITIVITY["haute"][0]

    # Un autre micro : redémarrage.
    prefs["value"] = prefs["value"].model_copy(update={"voice_input_device": "Casque USB"})
    await service.apply(prefs["value"])
    assert echo.loads[-1] == ("Casque USB", None)
    assert len(sources) == 2

    # Voix coupée : tout s'arrête et l'écho est déchargé.
    prefs["value"] = prefs["value"].model_copy(update={"voice_enabled": False})
    await service.apply(prefs["value"])
    assert service.snapshot()["state"] == "off"
    assert echo.unloads >= 2
    assert service.listen_now() is False
    await service.close()


def test_the_meta_profile_listens_with_muse_voice_transcribe_alone() -> None:
    prefs = {"value": Preferences(model_profile="auto")}
    service, _bus, _chat = make_service(prefs, echo=FakeEcho(available=True), sources=[])
    groq, cartesia = object(), object()
    service._parts.update({"cloud_stt": groq, "streaming_stt": cartesia})
    auto = Preferences(model_profile="auto")
    assert service._cloud_for(auto) is groq
    assert service._streaming_for(auto) is cartesia
    assert service._cloud_for(Preferences(model_profile="auto", voice_stt="local")) is None
    # « Tout Meta » : Muse Voice Transcribe est le moteur du jeu, sans Groq ni Cartesia en plus.
    for stt in ("groq", "cartesia", "local"):
        meta = Preferences(model_profile="meta", voice_stt=stt)
        assert service._cloud_for(meta) is None
        assert service._streaming_for(meta) is None


async def test_an_echo_micro_invisible_to_portaudio_falls_back_to_the_default() -> None:
    """Ubuntu 24.04 : PortAudio sans PulseAudio ne voit pas les micros virtuels de PipeWire."""
    prefs = {"value": Preferences(voice_input_device="alsa_input.usb")}
    echo = FakeEcho(available=True)
    sources: list[str | None] = []
    service, _bus, _chat = make_service(prefs, echo=echo, sources=sources)
    service._sees = lambda name, _kind: name not in {"ojarvis_micro", "alsa_input.usb"}
    await service.start()
    assert service.status.echo_cancel is False
    assert echo.unloads == 1
    assert sources == [None]  # micro par défaut du système, pas d'erreur
    assert service.status.error is None
    await service.close()


async def test_without_echo_cancel_only_hey_jarvis_interrupts() -> None:
    prefs = {"value": Preferences()}
    service, _bus, _chat = make_service(prefs, echo=FakeEcho(available=False), sources=[])
    await service.start()
    assert service._pipeline is not None
    assert service._pipeline.config.barge_in is False
    assert service.listen_now() is True
    assert await service.interrupt() is True
    await service.close()


async def test_a_lost_microphone_is_reported_then_retried() -> None:
    prefs = {"value": Preferences()}
    sources: list[str | None] = []
    service, _bus, _chat = make_service(
        prefs, echo=FakeEcho(available=False), sources=sources, broken=True
    )
    service.retry_delays_s = (0.01,)
    await service.start()
    await wait_until(lambda: len(sources) >= 2)
    await wait_until(lambda: service.status.error is not None)
    assert service.snapshot()["error"] == "Micro ou haut-parleur introuvable."
    await service.close()


def test_devices_come_from_pipewire_with_readable_names(monkeypatch: pytest.MonkeyPatch) -> None:
    listing = {
        "sources": [
            {
                "name": "alsa_input.pci",
                "description": "(null)",
                "properties": {"device.description": "Audio interne", "device.class": "sound"},
            },
            {"name": "alsa_output.pci.monitor", "properties": {"device.class": "monitor"}},
            {"name": "ojarvis_micro", "description": "O-jarvis micro", "properties": {}},
        ],
        "sinks": [{"name": "alsa_output.pci", "description": "Haut-parleurs", "properties": {}}],
    }

    def fake_run(args: list[str], **_: Any) -> Any:
        return types.SimpleNamespace(stdout=json.dumps(listing[args[-1]]))

    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/pactl")
    monkeypatch.setattr(subprocess, "run", fake_run)
    assert list_devices() == {
        "inputs": [{"name": "alsa_input.pci", "label": "Audio interne"}],
        "outputs": [{"name": "alsa_output.pci", "label": "Haut-parleurs"}],
    }


def test_switching_the_model_profile_reapplies_the_voice(
    client: TestClient, services: Services, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Revue du 27/10/2026 : couper « Tout Meta » laissait ta voix partir chez Meta.
    applied: list[str] = []

    async def record(prefs: Preferences) -> None:
        applied.append(prefs.model_profile)

    monkeypatch.setattr(services.voice, "apply", record)
    assert client.patch("/v1/settings", json={"model_profile": "meta"}).status_code == 200
    assert client.portal is not None
    client.portal.call(anyio.sleep, 0.05)  # la mise à jour part en arrière-plan
    assert applied == ["meta"]


def test_dictation_needs_the_voice_to_run(client: TestClient) -> None:
    # #10 : sans voix allumée (ici, pas d'audio), le terminal reçoit une explication claire.
    response = client.post("/v1/voice/dictate", json={})
    assert response.status_code == 409
    assert "La voix est éteinte" in response.json()["error"]["message"]
    assert client.post("/v1/voice/say", json={"text": "Bonjour."}).json() == {"spoken": False}
