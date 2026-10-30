"""SAM 3.1 (Meta Model API) : retrouver les éléments d'une image à partir d'un nom court.

On lui donne une capture et un concept en anglais (« button », « icon »…) ; il rend un
cadre par élément trouvé, au pixel près, en 1 s environ (essai du 30/10/2026 sur une
fenêtre d'application : les 8 boutons trouvés). Il ne lit pas le texte : « Exporter en PDF
button » ne trouve rien. Muse Spark lit ensuite les étiquettes et choisit (`locate.py`).

La capture part chez Meta en mode Standard : elle ne sert pas à entraîner ses modèles.
"""

from __future__ import annotations

import base64
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass

import httpx

from ojarvis.core.errors import UpstreamError
from ojarvis.core.logs import get_logger
from ojarvis.tracing.tracer import Tracer

log = get_logger(__name__)

SAM_URL = "https://api.meta.ai/v1/responses"
SAM_MODEL = "sam-3.1"
#: Prix public : 2,50 $ les 1 000 images.
PRICE_PER_IMAGE_USD = 0.0025
#: Un cadre, tel que SAM l'écrit : `0<|box;x1=96;y1=35;x2=218;y2=61;w=1280;h=720;c=0.76|>`.
BOX = re.compile(
    r"(\d+)<\|box;x1=(\d+);y1=(\d+);x2=(\d+);y2=(\d+);w=(\d+);h=(\d+)(?:;c=([\d.]+))?\|>"
)


@dataclass(frozen=True, slots=True)
class Box:
    """Un élément trouvé, en pixels de l'image envoyée (x2 et y2 compris)."""

    id: int
    x1: int
    y1: int
    x2: int
    y2: int
    confidence: float | None = None

    @property
    def center(self) -> tuple[int, int]:
        return (self.x1 + self.x2) // 2, (self.y1 + self.y2) // 2


@dataclass(frozen=True, slots=True)
class Segmentation:
    boxes: tuple[Box, ...]
    width: int
    height: int


def parse_boxes(text: str) -> tuple[list[Box], tuple[int, int] | None]:
    """Les cadres d'une réponse (les masques sont ignorés), et la taille de l'image."""
    boxes: list[Box] = []
    size: tuple[int, int] | None = None
    for match in BOX.finditer(text):
        ident, x1, y1, x2, y2, width, height = (int(v) for v in match.groups()[:7])
        confidence = float(match.group(8)) if match.group(8) else None
        boxes.append(Box(ident, x1, y1, x2, y2, confidence))
        size = (width, height)
    return boxes, size


class SamClient:
    def __init__(
        self,
        api_key: Callable[[], str | None],
        http: httpx.AsyncClient,
        tracer: Tracer | None = None,
        *,
        timeout_s: float = 30.0,
    ) -> None:
        self._api_key = api_key
        self._http = http
        self._tracer = tracer
        self._timeout_s = timeout_s

    @property
    def available(self) -> bool:
        return bool(self._api_key())

    async def find(self, image: bytes, concept: str, *, mime: str = "image/png") -> Segmentation:
        """Tous les éléments de l'image qui répondent au concept (aucun : une liste vide)."""
        key = self._api_key()
        if not key:
            raise UpstreamError("SAM 3.1 demande ta clé Meta (Réglages › Clés).")
        body = {
            "model": SAM_MODEL,
            "stream": True,
            "metadata": {"include_confidence": "true"},
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": concept},
                        {
                            "type": "input_image",
                            "image_url": f"data:{mime};base64,{base64.b64encode(image).decode()}",
                        },
                    ],
                }
            ],
        }
        started = time.perf_counter()
        if self._tracer is None:
            return await self._call(key, body)
        async with self._tracer.span(
            "segmentation SAM 3.1", kind="llm", provider="meta", model=SAM_MODEL, concept=concept
        ) as span:
            result = await self._call(key, body)
            span.set(
                objets=len(result.boxes),
                cost_usd=PRICE_PER_IMAGE_USD,
                duree_ms=round((time.perf_counter() - started) * 1000),
            )
            return result

    async def _call(self, key: str, body: dict[str, object]) -> Segmentation:
        text: list[str] = []
        try:
            async with self._http.stream(
                "POST",
                SAM_URL,
                json=body,
                headers={"Authorization": f"Bearer {key}", "Accept": "text/event-stream"},
                timeout=self._timeout_s,
            ) as response:
                if response.status_code != 200:
                    await response.aread()
                    raise UpstreamError(
                        f"SAM 3.1 a refusé la capture (HTTP {response.status_code})."
                    )
                async for line in response.aiter_lines():
                    if not line.startswith("data: ") or line == "data: [DONE]":
                        continue
                    event = json.loads(line.removeprefix("data: "))
                    if event.get("type") == "response.output_text.delta":
                        text.append(str(event.get("delta") or ""))
        except httpx.HTTPError as exc:
            raise UpstreamError(f"SAM 3.1 injoignable ({type(exc).__name__}).") from exc
        boxes, size = parse_boxes("".join(text))
        width, height = size or (0, 0)
        log.info("sam_segmented", objets=len(boxes))
        return Segmentation(tuple(boxes), width, height)
