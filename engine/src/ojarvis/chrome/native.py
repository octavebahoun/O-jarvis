"""La messagerie native de Chrome : chaque message est un objet JSON précédé de sa longueur,
sur 4 octets dans l'ordre de la machine."""

from __future__ import annotations

import json
import struct
from typing import Any, BinaryIO

#: Chrome refuse un message de plus de 1 Mo venant de l'hôte.
MAX_TO_CHROME = 1024 * 1024
#: Et l'hôte, un message de Chrome plus gros que ça (une page ou une capture y tient).
MAX_FROM_CHROME = 16 * 1024 * 1024


class NativeMessageError(Exception):
    pass


def read_message(stream: BinaryIO) -> dict[str, Any] | None:
    """Le message suivant de Chrome ; None quand Chrome a fermé le panneau."""
    header = stream.read(4)
    if len(header) < 4:
        return None
    (length,) = struct.unpack("=I", header)
    if length > MAX_FROM_CHROME:
        raise NativeMessageError("message trop long")
    body = stream.read(length)
    if len(body) < length:
        return None
    try:
        message = json.loads(body)
    except ValueError as error:
        raise NativeMessageError("message illisible") from error
    if not isinstance(message, dict):
        raise NativeMessageError("message illisible")
    return message


def encode(message: dict[str, Any]) -> bytes:
    body = json.dumps(message, ensure_ascii=False).encode()
    if len(body) > MAX_TO_CHROME:
        raise NativeMessageError("message trop long pour Chrome")
    return struct.pack("=I", len(body)) + body
