"""Les modèles vocaux à télécharger, selon le profil : mot de réveil, Whisper et Piper en
« Automatique » ; seulement la voix de Meta (MMS) en « Tout Meta ». Et les seuils du mot de
réveil selon la sensibilité choisie.
"""

from __future__ import annotations

from ojarvis.core.downloads import PinnedFile
from ojarvis.voice.stt import WHISPER_FILES
from ojarvis.voice.tts import PIPER_FILES
from ojarvis.voice.tts_meta import MMS_EN_FILES, MMS_FILES
from ojarvis.voice.wake import WAKE_FILES

ModelSets = dict[str, tuple[str, dict[str, PinnedFile]]]

MODEL_SETS: ModelSets = {
    "reveil": ("openwakeword", WAKE_FILES),
    "transcription": ("faster-whisper-base", WHISPER_FILES),
    "voix_locale": ("piper", PIPER_FILES),
}

#: « Tout Meta » : seules les voix de Meta (française et anglaise) se téléchargent.
META_MODEL_SETS: ModelSets = {
    "voix_meta": ("mms-tts-fra", MMS_FILES),
    "voix_meta_en": ("mms-tts-eng", MMS_EN_FILES),
}


def model_sets(profile: str) -> ModelSets:
    return META_MODEL_SETS if profile == "meta" else MODEL_SETS


#: Seuils du mot de réveil selon la sensibilité choisie dans Réglages.
SENSITIVITY: dict[str, tuple[float, float, bool]] = {
    "basse": (0.6, 0.3, False),
    "normale": (0.5, 0.15, True),
    "haute": (0.35, 0.1, True),
}
