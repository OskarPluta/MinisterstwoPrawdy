"""Images and screenshots: validate, read EXIF, normalise (strips metadata), OCR.

Not done, and said so to the user: no edit/manipulation detection and no search for earlier
copies of the image on the internet (the task forbids implying a reverse image search).
"""

from __future__ import annotations

import hashlib
import io
import logging
from datetime import datetime

from PIL import Image, ImageOps, UnidentifiedImageError

from rag.attachments import Attachment
from rag.timeutil import WARSAW, fmt_local

from .ocr import Ocr, OcrUnavailable

log = logging.getLogger(__name__)

MAX_IMAGE_BYTES = 10_000_000
MAX_PIXELS = 40_000_000
MAX_SIDE = 2000  # longest side sent to OCR
FORMATS = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp", "GIF": "image/gif",
           "BMP": "image/bmp", "TIFF": "image/tiff"}
Image.MAX_IMAGE_PIXELS = MAX_PIXELS  # Pillow raises DecompressionBombError above 2x this

LIMIT_OCR = "Tekst odczytano automatycznie i może zawierać błędy. Porównaj go z obrazem."
LIMIT_SCOPE = (
    "Nie sprawdzamy, czy obraz był edytowany, i nie szukamy jego wcześniejszych wystąpień w internecie."
)
NOTE_NO_EXIF = (
    "Obraz nie ma metadanych EXIF. To typowe dla zrzutów ekranu i zdjęć z komunikatorów, więc ich brak niczego nie dowodzi."
)
NOTE_EXIF_CAVEAT = "Metadane łatwo zmienić lub usunąć, więc traktuj je jako wskazówkę."
NOTE_GPS = "Zdjęcie zawiera dane o lokalizacji (GPS). Nie zapisujemy ich i nie przekazujemy modelowi."

_EXIF_IFD, _GPS_IFD = 0x8769, 0x8825
_DATETIME_ORIGINAL, _DATETIME, _MAKE, _MODEL, _OFFSET_ORIGINAL = 36867, 306, 271, 272, 36881


def sniff_image(data: bytes) -> str | None:
    """MIME type from the content (not the file name), or None."""
    try:
        with Image.open(io.BytesIO(data)) as im:
            return FORMATS.get(im.format or "")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        return None


def _exif_date(raw: object, offset: object) -> str | None:
    try:
        dt = datetime.strptime(str(raw).strip("\x00 "), "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None
    if isinstance(offset, str) and offset.strip():
        try:
            return datetime.fromisoformat(dt.isoformat() + offset.strip()).isoformat()
        except ValueError:
            pass
    return dt.replace(tzinfo=WARSAW).isoformat()  # EXIF has no zone; assume Polish local time


def dhash(im: Image.Image) -> str:
    """64-bit difference hash, for grouping repeated submissions of the same picture later."""
    g = im.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
    px = g.tobytes()  # mode L: one byte per pixel, row by row
    bits = "".join("1" if px[r * 9 + c] > px[r * 9 + c + 1] else "0" for r in range(8) for c in range(8))
    return f"{int(bits, 2):016x}"


def to_png(im: Image.Image) -> bytes:
    """Upright, RGB, at most MAX_SIDE px, re-encoded (drops EXIF, GPS and anything appended to the file)."""
    im = ImageOps.exif_transpose(im)
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        bg = Image.new("RGB", im.size, "white")
        bg.paste(im, mask=im.getchannel("A"))
        im = bg
    elif im.mode != "RGB":
        im = im.convert("RGB")
    im.thumbnail((MAX_SIDE, MAX_SIDE))
    out = io.BytesIO()
    im.save(out, "PNG", optimize=True)
    return out.getvalue()


def compose_text(r) -> str:
    """OCR result as the text block the extraction model sees."""
    lines: list[str] = []
    if r.description:
        lines.append(f"Opis obrazu (odczyt modelu, nie fakt): {r.description}")
    if r.platform:
        lines.append(f"Wygląda na: {r.platform}")
    if r.visible_author:
        lines.append(f"Widoczny autor: {r.visible_author}")
    if r.visible_dates:
        lines.append("Widoczne daty: " + "; ".join(r.visible_dates))
    if r.text.strip():
        lines.append("Tekst z obrazu:\n" + r.text.strip())
    return "\n".join(lines)


def read_image(data: bytes, label: str, ocr: Ocr) -> Attachment:
    a = Attachment(kind="image", label=label, meta={"size": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    if len(data) > MAX_IMAGE_BYTES:
        a.limitations.append(f"Obraz jest za duży (limit {MAX_IMAGE_BYTES // 1_000_000} MB).")
        return a
    try:
        im = Image.open(io.BytesIO(data))
        mime = FORMATS.get(im.format or "")
        if mime is None:
            a.limitations.append("Ten format obrazu nie jest obsługiwany (obsługujemy PNG, JPEG, WebP, GIF, BMP i TIFF).")
            return a
        if im.width * im.height > MAX_PIXELS:
            a.limitations.append("Obraz ma zbyt dużą rozdzielczość.")
            return a
        im.load()  # first frame of animations
    except (UnidentifiedImageError, Image.DecompressionBombError):
        a.limitations.append("Tego pliku nie da się otworzyć jako obrazu.")
        return a
    except OSError:
        a.limitations.append("Plik obrazu jest uszkodzony.")
        return a
    a.meta.update(mime=mime, width=im.width, height=im.height)

    exif = im.getexif()
    sub = exif.get_ifd(_EXIF_IFD) if exif else {}
    taken = _exif_date(sub.get(_DATETIME_ORIGINAL) or exif.get(_DATETIME), sub.get(_OFFSET_ORIGINAL)) if exif else None
    camera = " ".join(str(exif.get(t, "")).strip("\x00 ") for t in (_MAKE, _MODEL)).strip() if exif else ""
    if taken:
        a.material_date, a.material_date_kind = taken, "taken"
    if taken or camera:
        parts = ([f"wykonano {fmt_local(taken)}"] if taken else []) + ([f"aparat: {camera}"] if camera else [])
        a.notes.append("Metadane zdjęcia (EXIF): " + ", ".join(parts) + ". " + NOTE_EXIF_CAVEAT)
    else:
        a.notes.append(NOTE_NO_EXIF)
    if exif and exif.get_ifd(_GPS_IFD):
        a.notes.append(NOTE_GPS)

    png = to_png(im)
    a.meta["dhash"] = dhash(im)
    a.limitations.append(LIMIT_SCOPE)
    try:
        r = ocr.read(png)
    except OcrUnavailable as e:
        a.limitations.insert(0, f"Nie udało się odczytać tekstu z obrazu. {e.message}")
        return a
    a.method = r.method
    a.text = compose_text(r)
    a.limitations[:0] = [LIMIT_OCR, *r.limitations]
    if not r.text.strip():
        a.limitations.insert(0, "Na obrazie nie znaleźliśmy czytelnego tekstu.")
    if r.contains_instructions:
        a.meta["contains_instructions"] = True
    return a
