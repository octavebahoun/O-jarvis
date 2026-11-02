"""Modèles locaux : où ils vont, ce qui manque, et l'import de fichiers téléchargés à la main.

Pour une connexion faible, chaque fichier peut être téléchargé dans un navigateur puis
importé : il est reconnu par son empreinte SHA-256 (le nom donné par le navigateur ne
compte pas) et rangé à sa place. Un fichier inconnu est supprimé, jamais chargé.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anyio

from ojarvis.core.downloads import ChecksumError, PinnedFile, files_present
from ojarvis.memory.embeddings import E5_FILES
from ojarvis.voice.stt import WHISPER_FILES
from ojarvis.voice.tts import PIPER_FILES
from ojarvis.voice.wake import WAKE_FILES


@dataclass(frozen=True)
class ModelSet:
    key: str
    label: str
    folder: str
    files: dict[str, PinnedFile]


#: Les clés des modèles vocaux sont celles de `VoiceService.status.models`.
#: Les voix de Meta (MMS) s'y ajoutent avec O-jarvis Shell.
LOCAL_MODELS: list[ModelSet] = [
    ModelSet("reveil", "Mot de réveil", "openwakeword", WAKE_FILES),
    ModelSet("transcription", "Transcription", "faster-whisper-base", WHISPER_FILES),
    ModelSet("voix_locale", "Voix locale", "piper", PIPER_FILES),
    ModelSet("memoire", "Mémoire", "multilingual-e5-small", E5_FILES),
]

#: Au-delà, l'import est refusé : aucun modèle épinglé n'est aussi gros.
MAX_IMPORT_BYTES = 1 << 30


@dataclass(frozen=True)
class Imported:
    key: str
    label: str
    file: str


def model_set(key: str) -> ModelSet | None:
    return next((m for m in LOCAL_MODELS if m.key == key), None)


def describe(models_dir: Path) -> list[dict[str, Any]]:
    """Pour chaque modèle : dossier, fichiers, liens et ce qui est déjà sur le disque."""
    result = []
    for model in LOCAL_MODELS:
        folder = models_dir / model.folder
        files = []
        for name, pinned in model.files.items():
            target = folder / name
            partial = target.with_name(name + ".part")
            files.append(
                {
                    "name": name,
                    "url": pinned.url,
                    "sha256": pinned.sha256,
                    "size": pinned.size,
                    "present": target.is_file(),
                    "partial": partial.stat().st_size if partial.is_file() else 0,
                }
            )
        result.append(
            {
                "key": model.key,
                "label": model.label,
                "folder": str(folder),
                "ready": files_present(folder, model.files),
                "files": files,
            }
        )
    return result


def _index() -> dict[str, tuple[ModelSet, str]]:
    return {
        pinned.sha256: (model, name)
        for model in LOCAL_MODELS
        for name, pinned in model.files.items()
    }


async def import_file(models_dir: Path, chunks: AsyncIterator[bytes]) -> Imported:
    """Reçoit un fichier, le reconnaît par son empreinte et le range dans son dossier."""
    await anyio.Path(models_dir).mkdir(parents=True, exist_ok=True)
    temporary = models_dir / f".import-{uuid.uuid4().hex}.part"
    digest = hashlib.sha256()
    size = 0
    try:
        with temporary.open("wb") as handle:
            async for chunk in chunks:
                size += len(chunk)
                if size > MAX_IMPORT_BYTES:
                    raise ChecksumError("fichier trop gros pour être un modèle de Jarvis")
                digest.update(chunk)
                handle.write(chunk)
        found = _index().get(digest.hexdigest())
        if found is None:
            raise ChecksumError("ce fichier ne correspond à aucun modèle attendu")
        model, name = found
        folder = models_dir / model.folder
        await anyio.Path(folder).mkdir(parents=True, exist_ok=True)
        temporary.replace(folder / name)
        (folder / f"{name}.part").unlink(missing_ok=True)
        return Imported(model.key, model.label, name)
    finally:
        temporary.unlink(missing_ok=True)
