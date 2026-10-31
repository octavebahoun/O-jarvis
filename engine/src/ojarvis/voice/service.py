"""Service vocal : démarre, arrête et reconfigure le pipeline selon les réglages.

Il est découplé du chat : sans micro, sans modèle ou sur une erreur, le reste du moteur
continue ; l'état et la raison sont visibles dans l'interface. Les modèles locaux
(mot de réveil, transcription, voix de secours) sont téléchargés une fois et vérifiés.

Profil « Tout Meta » : aucun modèle qui ne soit pas de Meta. L'écoute passe par Muse Voice
Transcribe, la voix est MMS (Meta, lancée ici) puis espeak-ng (la voix du système), la parole
est reconnue à son volume, et l'on parle après le raccourci, le bouton micro ou « /voix ».
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Callable, Coroutine
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import anyio.to_thread
import httpx

from ojarvis.chat.service import ChatService
from ojarvis.core.downloads import fetch_pinned, files_present
from ojarvis.core.logs import get_logger
from ojarvis.events.bus import EventBus
from ojarvis.security.secrets import SecretStore
from ojarvis.settings_store import Preferences
from ojarvis.tracing.tracer import Tracer
from ojarvis.voice.audio import (
    AudioSink,
    AudioSource,
    MicrophoneSource,
    SpeakerSink,
)
from ojarvis.voice.devices import (
    EC_SINK,
    EC_SOURCE,
    EchoCancel,
    _describe,
    _device_prefs,
    _portaudio_sees,
    _refresh_devices,
)
from ojarvis.voice.model_sets import SENSITIVITY, ModelSets, model_sets
from ojarvis.voice.pipeline import VoicePipeline
from ojarvis.voice.states import VoiceConfig, VoiceState
from ojarvis.voice.stt import CloudTranscriber, WhisperTranscriber
from ojarvis.voice.stt_online import (
    CartesiaSTT,
    GroqTranscriber,
    MetaTranscriber,
    StreamingTranscriber,
)
from ojarvis.voice.tts import CARTESIA_VERSION, CartesiaTTS, FallbackTTS, PiperTTS
from ojarvis.voice.tts_meta import EspeakTTS, MmsVoices
from ojarvis.voice.turn import TurnReport
from ojarvis.voice.vad import EndpointConfig, EnergyVad, SileroVad
from ojarvis.voice.wake import OpenWakeWord

log = get_logger(__name__)


RETRY_DELAYS_S: tuple[float, ...] = (5.0, 15.0, 60.0)


@dataclass
class VoiceStatus:
    state: str = VoiceState.OFF.value
    enabled: bool = False
    error: str | None = None
    models: dict[str, str] = field(default_factory=dict)
    echo_cancel: bool = False
    tts_engine: str | None = None
    tts_fallback: str | None = None
    stt_engine: str | None = None
    #: La transcription en ligne était voulue mais Whisper local a pris le relais.
    stt_fallback: bool = False
    last_latency_ms: float | None = None
    last_turn_at: float | None = None


class VoiceService:
    def __init__(
        self,
        *,
        models_dir: Path,
        http: httpx.AsyncClient,
        bus: EventBus,
        tracer: Tracer,
        chat: ChatService,
        secrets: SecretStore,
        preferences: Callable[[], Preferences],
        audio: bool = True,
        parts: dict[str, Any] | None = None,
        source_factory: Callable[[str | None], AudioSource] = MicrophoneSource,
        sink_factory: Callable[[str | None], AudioSink] = SpeakerSink,
        echo: EchoCancel | None = None,
        clock: Callable[[], float] = time.monotonic,
        on_listen: Callable[[], None] | None = None,
    ) -> None:
        self._on_listen = on_listen
        self._source_factory = source_factory
        #: Le micro ou la sortie est-il visible de PortAudio ? (toujours, hors vrai micro)
        self._sees: Callable[[str, str], bool] = (
            _portaudio_sees if source_factory is MicrophoneSource else lambda _name, _kind: True
        )
        self._sink_factory = sink_factory
        self._clock = clock
        self._models = models_dir
        self._http = http
        self._bus = bus
        self._tracer = tracer
        self._chat = chat
        self._secrets = secrets
        self._preferences = preferences
        self._audio = audio
        self.status = VoiceStatus()
        self._pipeline: VoicePipeline | None = None
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._echo = echo or EchoCancel()
        #: Composants chargés une fois par profil (modèles) ; fournis directement par les tests.
        self._parts: dict[str, Any] = dict(parts or {})
        self._parts_profile: str | None = None
        self._preloaded = parts is not None
        self._applied: dict[str, Any] = {}
        self._voices_cache: tuple[float, list[dict[str, Any]]] | None = None
        self._background: set[asyncio.Future[Any]] = set()
        self.retry_delays_s = RETRY_DELAYS_S

    # ------------------------------------------------------------------ état

    def _spawn(self, coroutine: Coroutine[Any, Any, Any]) -> None:
        task = asyncio.ensure_future(coroutine)
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    def snapshot(self) -> dict[str, Any]:
        data = asdict(self.status)
        data["frames"] = self._pipeline.frames_seen if self._pipeline is not None else 0
        if self._pipeline is not None and self.status.error is None:
            data["state"] = self._pipeline.state.value
        return data

    async def _publish(self) -> None:
        await self._bus.publish("voice.status", self.snapshot(), persist=False)

    def _model_status(self, profile: str) -> dict[str, str]:
        return {
            key: "prêt" if files_present(self._models / folder, files) else "absent"
            for key, (folder, files) in model_sets(profile).items()
        }

    # ------------------------------------------------------------------ démarrage

    async def start(self) -> None:
        async with self._lock:
            await self._start_locked()

    async def _start_locked(self) -> None:
        prefs = self._preferences()
        self.status.enabled = prefs.voice_enabled
        sets = model_sets(prefs.model_profile)
        self.status.models = (
            dict.fromkeys(sets, "simulé")
            if self._preloaded
            else self._model_status(prefs.model_profile)
        )
        if not prefs.voice_enabled or not self._audio:
            self.status.state = VoiceState.OFF.value
            self.status.error = None if self._audio else "Audio désactivé pour ce lancement."
            await self._publish()
            return
        self.status.state = "starting"
        self.status.error = None
        await self._publish()
        try:
            if not self._preloaded:
                await self._ensure_models(sets)
                await self._load_parts(prefs.model_profile)
            await self._launch(prefs)
        except Exception as exc:
            log.exception("voice_start_failed")
            self.status.state = VoiceState.ERROR.value
            self.status.error = _describe(exc)
            await self._publish()

    async def _ensure_models(self, sets: ModelSets) -> None:
        for key, (folder, files) in sets.items():
            target = self._models / folder
            if files_present(target, files):
                continue
            last = [0.0]

            def progress(
                _name: str, done: int, total: int | None, key: str = key, last: list[float] = last
            ) -> None:
                if total and time.monotonic() - last[0] > 1.0:
                    last[0] = time.monotonic()
                    self.status.models[key] = f"téléchargement {done * 100 // total} %"
                    self._spawn(self._publish())

            self.status.models[key] = "téléchargement"
            await self._publish()
            await fetch_pinned(self._http, target, files, on_progress=progress)
            self.status.models[key] = "prêt"
        await self._publish()

    async def _load_parts(self, profile: str) -> None:
        if self._parts and self._parts_profile == profile:
            return
        tts = self._parts.get("tts")
        if tts is not None:
            await tts.close()
        # Un seul jeu en mémoire : celui de l'autre profil est libéré.
        self._parts = {}
        self._parts = await (self._meta_parts() if profile == "meta" else self._auto_parts())
        self._parts_profile = profile

    async def _meta_parts(self) -> dict[str, Any]:
        mms = MmsVoices(self._models)
        await mms.warm_up()
        return {
            "wake": None,
            "vad": EnergyVad(),
            "stt": MetaTranscriber(lambda: self._secrets.get("MODEL_API_KEY"), self._http),
            "tts": FallbackTTS(mms, EspeakTTS(), on_fallback=self._on_tts_fallback),
        }

    async def _auto_parts(self) -> dict[str, Any]:
        wake = await anyio.to_thread.run_sync(OpenWakeWord, self._models / "openwakeword")
        vad = await anyio.to_thread.run_sync(SileroVad)
        stt = WhisperTranscriber(self._models / "faster-whisper-base")
        await anyio.to_thread.run_sync(stt.load)
        piper = PiperTTS(self._models / "piper")
        await piper.warm_up()
        cartesia = CartesiaTTS(
            lambda: self._secrets.get("CARTESIA_API_KEY"),
            voice=lambda: self._preferences().voice_name,
        )
        tts = FallbackTTS(
            cartesia,
            piper,
            on_fallback=self._on_tts_fallback,
            prefer_secondary=lambda: self._preferences().voice_engine == "piper",
        )
        return {
            "wake": wake,
            "vad": vad,
            "stt": stt,
            "tts": tts,
            "cloud_stt": GroqTranscriber(lambda: self._secrets.get("GROQ_API_KEY"), self._http),
            "streaming_stt": CartesiaSTT(lambda: self._secrets.get("CARTESIA_API_KEY")),
        }

    def _on_tts_fallback(self, reason: str) -> None:
        self.status.tts_fallback = reason
        tts = self._parts.get("tts")
        engine = getattr(getattr(tts, "secondary", None), "name", "piper")
        self._spawn(self._bus.publish("voice.tts_fallback", {"reason": reason, "engine": engine}))

    def _config(self, prefs: Preferences, *, echo_cancel: bool) -> VoiceConfig:
        threshold, candidate, verify = SENSITIVITY[prefs.wake_sensitivity]
        return VoiceConfig(
            wake_threshold=threshold,
            wake_candidate=candidate,
            verify_wake=verify,
            follow_up_s=6.0 if prefs.voice_follow_up else 0.0,
            # Sans annulation d'écho, Jarvis s'entendrait parler : seule la phrase
            # « Hey Jarvis » peut alors le couper (sauf casque, réglage explicite).
            barge_in=prefs.voice_barge_in and (echo_cancel or prefs.voice_headset),
            endpoint=EndpointConfig(),
        )

    async def _launch(self, prefs: Preferences) -> None:
        echo = await anyio.to_thread.run_sync(
            self._echo.load, prefs.voice_input_device, prefs.voice_output_device
        )
        if echo and self._source_factory is MicrophoneSource:
            await anyio.to_thread.run_sync(_refresh_devices)
        if echo:
            seen = await anyio.to_thread.run_sync(
                lambda: self._sees(EC_SOURCE, "input") and self._sees(EC_SINK, "output")
            )
            if not seen:
                # PortAudio sans accès à PulseAudio (Ubuntu 24.04) : le micro virtuel
                # existe mais reste invisible. On s'en passe plutôt que de planter.
                log.warning("echo_cancel_invisible")
                await anyio.to_thread.run_sync(self._echo.unload)
                echo = False
        self.status.echo_cancel = echo

        def device(name: str | None, kind: str) -> str | None:
            if name is None or self._sees(name, kind):
                return name
            log.warning("audio_device_invisible", kind=kind)
            return None  # périphérique par défaut du système

        sink = self._sink_factory(EC_SINK if echo else device(prefs.voice_output_device, "output"))

        def make_pipeline() -> VoicePipeline:
            current = self._preferences()
            return VoicePipeline(
                source=self._source_factory(
                    EC_SOURCE if echo else device(current.voice_input_device, "input")
                ),
                sink=sink,
                wake=self._parts["wake"],
                vad=self._parts["vad"],
                stt=self._parts["stt"],
                tts=self._parts["tts"],
                chat=self._chat,
                bus=self._bus,
                tracer=self._tracer,
                config=self._config(current, echo_cancel=echo),
                on_turn=self._on_turn,
                clock=self._clock,
                streaming_stt=self._streaming_for(current),
                cloud_stt=self._cloud_for(current),
            )

        self._pipeline = make_pipeline()
        self._applied = _device_prefs(prefs)
        self._task = asyncio.create_task(self._run(make_pipeline, sink))
        self.status.state = VoiceState.IDLE.value
        await self._publish()

    async def _run(self, make_pipeline: Callable[[], VoicePipeline], sink: AudioSink) -> None:
        """Fait tourner le pipeline ; s'il tombe (micro débranché…), réessaie un peu plus tard."""
        attempt = 0
        try:
            while True:
                pipeline = self._pipeline or make_pipeline()
                self._pipeline = pipeline
                started = time.monotonic()
                try:
                    await pipeline.run()
                    return
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    log.exception("voice_pipeline_failed")
                    self.status.state = VoiceState.ERROR.value
                    self.status.error = _describe(exc)
                    await self._publish()
                if time.monotonic() - started > 120:
                    attempt = 0
                delays = self.retry_delays_s
                if attempt >= len(delays):
                    return
                await asyncio.sleep(delays[attempt])
                attempt += 1
                self._pipeline = make_pipeline()
                self.status.error = None
                self.status.state = VoiceState.IDLE.value
                await self._publish()
        finally:
            await sink.close()

    def _streaming_for(self, prefs: Preferences) -> StreamingTranscriber | None:
        """Cartesia en flux : choisi, ou en relais de Groq si sa clé manque."""
        if prefs.voice_stt == "local" or prefs.model_profile == "meta":
            return None
        engine: StreamingTranscriber | None = self._parts.get("streaming_stt")
        return engine

    def _cloud_for(self, prefs: Preferences) -> CloudTranscriber | None:
        """Par phrase : Groq (Whisper large-v3), Whisper local restant le dernier secours.
        En « Tout Meta », Muse Voice Transcribe est la seule transcription : rien en plus."""
        if prefs.voice_stt != "groq" or prefs.model_profile == "meta":
            return None
        engine: CloudTranscriber | None
        engine = self._parts.get("cloud_stt")
        return engine

    def _on_turn(self, report: TurnReport) -> None:
        self.status.last_latency_ms = report.latency_ms
        self.status.last_turn_at = time.time()
        self.status.tts_engine = report.tts_engine
        self.status.stt_engine = report.stt_engine
        pipeline = self._pipeline
        wanted = pipeline is not None and (
            pipeline.streaming_stt is not None or pipeline.cloud_stt is not None
        )
        self.status.stt_fallback = wanted and report.stt_engine != "cartesia"
        if report.tts_engine in ("cartesia", "mms"):
            self.status.tts_fallback = None

    # ------------------------------------------------------------------ arrêt et réglages

    async def stop(self) -> None:
        async with self._lock:
            await self._stop_locked()
            self.status.state = VoiceState.OFF.value
            await self._publish()

    async def _stop_locked(self) -> None:
        task, self._task = self._task, None
        self._pipeline = None
        if task is not None:
            task.cancel()
            with contextlib.suppress(BaseException):
                await task
        await anyio.to_thread.run_sync(self._echo.unload)

    async def close(self) -> None:
        await self.stop()
        tts = self._parts.get("tts")
        if tts is not None:
            await tts.close()

    async def apply(self, prefs: Preferences) -> None:
        """Nouveaux réglages : redémarre seulement si c'est nécessaire."""
        async with self._lock:
            running = self._task is not None and not self._task.done()
            if not prefs.voice_enabled:
                if running:
                    await self._stop_locked()
                self.status.enabled = False
                self.status.state = VoiceState.OFF.value
                await self._publish()
                return
            if running and _device_prefs(prefs) == self._applied and self._pipeline is not None:
                self._pipeline.config = self._config(prefs, echo_cancel=self.status.echo_cancel)
                self._pipeline.streaming_stt = self._streaming_for(prefs)
                self._pipeline.cloud_stt = self._cloud_for(prefs)
                await self._publish()
                return
            if running:
                await self._stop_locked()
            await self._start_locked()

    # ------------------------------------------------------------------ commandes

    def listen_now(self) -> bool:
        if self._pipeline is None:
            return False
        self._pipeline.listen_now()
        if self._on_listen is not None:
            self._on_listen()
        return True

    async def dictate(self, timeout_s: float) -> str | None:
        """Une phrase dite pour le terminal (#10) : None si la voix ne tourne pas, « » si rien
        n'a été entendu à temps."""
        pipeline = self._pipeline
        if pipeline is None:
            return None
        future = pipeline.dictate()
        try:
            return await asyncio.wait_for(asyncio.shield(future), timeout_s)
        except TimeoutError:
            pipeline.cancel_dictation()
            return ""

    def say(self, text: str) -> bool:
        """Faire dire un texte à Jarvis (briefing) ; faux si la voix est éteinte ou occupée."""
        if self._pipeline is None:
            return False
        return self._pipeline.say(text)

    async def interrupt(self) -> bool:
        if self._pipeline is None:
            return False
        await self._pipeline.interrupt(by="interface")
        return True

    async def voices(self) -> list[dict[str, Any]]:
        """Voix françaises de Cartesia (mises en cache dix minutes)."""
        if self._voices_cache and time.monotonic() - self._voices_cache[0] < 600:
            return self._voices_cache[1]
        key = self._secrets.get("CARTESIA_API_KEY")
        if not key:
            return []
        response = await self._http.get(
            "https://api.cartesia.ai/voices",
            params={"language": "fr", "limit": 100},
            headers={"X-API-Key": key, "Cartesia-Version": CARTESIA_VERSION},
        )
        response.raise_for_status()
        body = response.json()
        items = body.get("data", body) if isinstance(body, dict) else body
        voices = [
            {
                "id": str(v["id"]),
                "name": str(v.get("name", "")),
                "gender": v.get("gender"),
                "description": str(v.get("description") or "")[:160],
            }
            for v in items
            if isinstance(v, dict) and v.get("language") in (None, "fr")
        ]
        self._voices_cache = (time.monotonic(), voices)
        return voices
