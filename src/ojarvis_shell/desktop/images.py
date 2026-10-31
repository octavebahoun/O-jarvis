"""Les images du repérage à l'écran (#48) : cadres numérotés et colorés, zone agrandie, zoom
sur un élément, capture annotée, et la comparaison faite avant un clic. Du dessin et du
calcul (Pillow), pas un modèle.
"""

from __future__ import annotations

import io
from typing import TYPE_CHECKING

from ojarvis.desktop.sam import Box

if TYPE_CHECKING:
    from ojarvis.desktop.locate import Mark

#: Des couleurs bien distinctes : un numéro se rattache sans erreur à son cadre.
PALETTE = (
    (230, 0, 160),
    (0, 110, 230),
    (230, 120, 0),
    (130, 0, 210),
    (0, 150, 140),
    (200, 30, 30),
)

GREEN = (20, 170, 70)


def _overlap(a: Box, b: Box) -> float:
    """La part commune de deux cadres (intersection sur union)."""
    width = min(a.x2, b.x2) - max(a.x1, b.x1) + 1
    height = min(a.y2, b.y2) - max(a.y1, b.y1) + 1
    if width <= 0 or height <= 0:
        return 0.0
    inter = width * height
    area = (a.x2 - a.x1 + 1) * (a.y2 - a.y1 + 1) + (b.x2 - b.x1 + 1) * (b.y2 - b.y1 + 1)
    return inter / (area - inter)


def refused_boxes(boxes: list[Box], refused: dict[int, str]) -> list[Box]:
    return [boxes[i] for i in refused if 0 <= i < len(boxes)]


def reading_order(boxes: list[Box]) -> list[Box]:
    """Les cadres sans doublon, ligne par ligne, de gauche à droite : deux voisins portent
    des numéros qui se suivent (« celui de gauche » se lit sans détour)."""
    unique: list[Box] = []
    for box in boxes:
        if all(_overlap(box, kept) < SAME_ELEMENT for kept in unique):
            unique.append(box)
    rows: list[list[Box]] = []
    for box in sorted(unique, key=lambda b: b.center[1]):
        if rows and abs(rows[-1][0].center[1] - box.center[1]) <= max(8, (box.y2 - box.y1) // 2):
            rows[-1].append(box)
        else:
            rows.append([box])
    return [box for row in rows for box in sorted(row, key=lambda b: b.x1)]


def draw_numbers(image: bytes, boxes: list[Box]) -> bytes:
    """La capture, chaque cadre de sa couleur avec son numéro (1, 2, 3…) : pour Muse Spark."""
    from PIL import Image, ImageDraw, ImageFont  # noqa: PLC0415

    with Image.open(io.BytesIO(image)) as source:
        canvas = source.convert("RGB")
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default(size=12)
    for number, box in enumerate(boxes, start=1):
        color = PALETTE[(number - 1) % len(PALETTE)]
        draw.rectangle((box.x1, box.y1, box.x2, box.y2), outline=color, width=2)
        label = str(number)
        left, top, right, bottom = draw.textbbox((0, 0), label, font=font)
        tag_w, tag_h = right - left + 4, bottom - top + 4
        # L'étiquette au-dessus du cadre quand il y a la place, sinon dedans.
        y = box.y1 - tag_h if box.y1 >= tag_h else box.y1
        draw.rectangle((box.x1, y, box.x1 + tag_w, y + tag_h), fill=color)
        draw.text((box.x1 + 2 - left, y + 2 - top), label, fill="white", font=font)
    out = io.BytesIO()
    canvas.save(out, "JPEG", quality=88)
    return out.getvalue()


def _mime(image: bytes) -> str:
    """Le type déclaré doit être celui des octets (la capture brute est un PNG)."""
    return "image/png" if image.startswith(b"\x89PNG") else "image/jpeg"


def image_size(image: bytes) -> tuple[int, int]:
    from PIL import Image  # noqa: PLC0415

    with Image.open(io.BytesIO(image)) as source:
        width, height = source.size
    return width, height


def area(point: tuple[int, int], size: tuple[int, int]) -> tuple[int, int, int, int]:
    """La zone autour du point désigné, dans l'écran : (gauche, haut, droite, bas)."""
    half_w, half_h = AREA_HALF
    width, height = size
    x = min(max(point[0], 0), width - 1)
    y = min(max(point[1], 0), height - 1)
    left, top = max(x - half_w, 0), max(y - half_h, 0)
    return left, top, min(left + 2 * half_w, width - 1), min(top + 2 * half_h, height - 1)


def in_area(box: Box, zone: tuple[int, int, int, int]) -> bool:
    x, y = box.center
    return zone[0] <= x <= zone[2] and zone[1] <= y <= zone[3]


def enlarge(image: bytes, boxes: list[Box], zone: tuple[int, int, int, int]) -> bytes:
    """La zone agrandie deux fois, ses cadres numérotés : des icônes lisibles, sans voisins
    dont les étiquettes se chevauchent."""
    from PIL import Image  # noqa: PLC0415

    left, top, right, bottom = zone
    with Image.open(io.BytesIO(image)) as source:
        part = source.convert("RGB").crop((left, top, right + 1, bottom + 1))
    part = part.resize((part.width * AREA_ZOOM, part.height * AREA_ZOOM), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    part.save(out, "PNG")
    moved = [
        Box(
            box.id,
            (box.x1 - left) * AREA_ZOOM,
            (box.y1 - top) * AREA_ZOOM,
            (box.x2 - left) * AREA_ZOOM,
            (box.y2 - top) * AREA_ZOOM,
        )
        for box in boxes
    ]
    return draw_numbers(out.getvalue(), moved)


def zoom(image: bytes, box: Box) -> bytes:
    """L'élément et ses abords, agrandis trois fois, l'élément encadré en vert."""
    from PIL import Image, ImageDraw  # noqa: PLC0415

    with Image.open(io.BytesIO(image)) as source:
        width, height = source.size
        margin_x = max(box.x2 - box.x1, 40)
        margin_y = max(box.y2 - box.y1, 24)
        left, top = max(box.x1 - margin_x, 0), max(box.y1 - margin_y, 0)
        right, bottom = min(box.x2 + margin_x, width - 1), min(box.y2 + margin_y, height - 1)
        part = source.convert("RGB").crop((left, top, right + 1, bottom + 1))
    part = part.resize((part.width * ZOOM, part.height * ZOOM), Image.Resampling.LANCZOS)
    draw = ImageDraw.Draw(part)
    frame = (
        (box.x1 - left) * ZOOM - 3,
        (box.y1 - top) * ZOOM - 3,
        (box.x2 - left + 1) * ZOOM + 2,
        (box.y2 - top + 1) * ZOOM + 2,
    )
    draw.rectangle(frame, outline=GREEN, width=3)
    out = io.BytesIO()
    part.save(out, "JPEG", quality=90)
    return out.getvalue()


def crop(image: bytes, box: Box) -> bytes:
    from PIL import Image  # noqa: PLC0415

    with Image.open(io.BytesIO(image)) as source:
        part = source.convert("L").crop((box.x1, box.y1, box.x2 + 1, box.y2 + 1))
    out = io.BytesIO()
    part.save(out, "PNG")
    return out.getvalue()


def unchanged(mark: Mark, image: bytes) -> bool:
    """L'élément est-il toujours là, à la même place, sur cette nouvelle capture ?"""
    from PIL import Image, ImageChops  # noqa: PLC0415

    with Image.open(io.BytesIO(image)) as source:
        if source.size != (mark.width, mark.height):
            return False
        box = mark.box
        now = source.convert("L").crop((box.x1, box.y1, box.x2 + 1, box.y2 + 1))
    with Image.open(io.BytesIO(mark.crop)) as before:
        difference = ImageChops.difference(before.convert("L"), now)
    counts = difference.histogram()  # combien de pixels pour chaque écart, de 0 à 255
    total = sum(counts)
    return not total or sum(counts[CHANGED_LEVEL + 1 :]) / total <= CHANGED_SHARE


def draw_choice(image: bytes, box: Box) -> bytes:
    """La capture, l'élément choisi encadré en vert et l'endroit du clic marqué : pour toi.
    Le cadre est posé autour de l'élément, sans le couvrir : on voit ce qui sera cliqué."""
    from PIL import Image, ImageDraw  # noqa: PLC0415

    with Image.open(io.BytesIO(image)) as source:
        canvas = source.convert("RGB")
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((box.x1 - 4, box.y1 - 4, box.x2 + 4, box.y2 + 4), outline=GREEN, width=3)
    x, y = box.center
    draw.ellipse((x - 2, y - 2, x + 2, y + 2), fill=GREEN)
    out = io.BytesIO()
    canvas.save(out, "JPEG", quality=85)
    return out.getvalue()


#: Avant le clic, l'endroit est comparé à la capture d'origine : au-delà de cette part de
#: pixels changés (une fenêtre passée devant, la page qui a défilé), Jarvis ne clique pas.
CHANGED_SHARE = 0.3

CHANGED_LEVEL = 40

#: Deux cadres qui se recouvrent à ce point (concepts différents) : le même élément.
SAME_ELEMENT = 0.6

ZOOM = 3

#: La zone autour du point désigné (demi-largeur, demi-hauteur), agrandie deux fois.
AREA_HALF = (180, 70)

AREA_ZOOM = 2
