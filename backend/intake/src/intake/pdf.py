"""PDF documents: text layer (pypdf), OCR of the first pages for scans (pypdfium2 renders them).

Nothing in the file is executed (no JavaScript, no forms). File metadata is context only:
it is easy to change and never decides a verdict.
"""

from __future__ import annotations

import hashlib
import io
import logging
from datetime import timedelta

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from rag.attachments import Attachment
from rag.timeutil import fmt_local

from .image import to_png
from .ocr import Ocr, OcrUnavailable

log = logging.getLogger(__name__)

MAX_PDF_BYTES = 15_000_000
MAX_TEXT_PAGES = 30
OCR_PAGES = 3
MIN_TEXT_CHARS = 100  # less than this in the text layer: treat as a scan
RENDER_SCALE = 2.0  # 144 dpi

NOTE_META_CAVEAT = "Metadane pliku łatwo zmienić, więc traktuj je jako wskazówkę."


def _ocr_pages(data: bytes, pages: int, ocr: Ocr) -> tuple[str, str]:
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(data)
    try:
        texts, method = [], ""
        for i in range(min(pages, OCR_PAGES)):
            img = doc[i].render(scale=RENDER_SCALE).to_pil()
            r = ocr.read(to_png(img))
            method = r.method
            if r.text.strip():
                texts.append(f"[strona {i + 1}]\n{r.text.strip()}")
        return "\n\n".join(texts), method
    finally:
        doc.close()


def read_pdf(data: bytes, label: str, ocr: Ocr) -> Attachment:
    a = Attachment(kind="pdf", label=label, meta={"size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                                                 "mime": "application/pdf"})
    if len(data) > MAX_PDF_BYTES:
        a.limitations.append(f"Dokument jest za duży (limit {MAX_PDF_BYTES // 1_000_000} MB).")
        return a
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            a.limitations.append("Dokument jest zabezpieczony hasłem, więc nie możemy go odczytać.")
            return a
        pages = len(reader.pages)
        texts = []
        for i, page in enumerate(reader.pages[:MAX_TEXT_PAGES]):
            t = (page.extract_text() or "").strip()
            if t:
                texts.append(f"[strona {i + 1}]\n{t}")
        text = "\n\n".join(texts)
        meta = reader.metadata
    except (PdfReadError, ValueError, KeyError, TypeError, OSError) as e:
        log.info("pdf read failed: %r", e)
        a.limitations.append("Tego pliku nie da się odczytać jako dokumentu PDF.")
        return a
    a.meta["pages"] = pages

    if meta is not None:
        try:
            created, modified = meta.creation_date, meta.modification_date
        except (ValueError, TypeError):
            created = modified = None
        producer = (meta.producer or meta.creator or "").strip() if meta else ""
        parts = []
        if created:
            a.material_date, a.material_date_kind = created.isoformat(), "created"
            parts.append(f"utworzono {fmt_local(created.isoformat())}")
        if modified and created and modified - created > timedelta(days=1):
            parts.append(f"zmieniono {fmt_local(modified.isoformat())}")
        if producer:
            parts.append(f"program: {producer[:80]}")
        if parts:
            a.notes.append("Metadane pliku: " + ", ".join(parts) + ". " + NOTE_META_CAVEAT)

    a.method = "pdf_text"
    if pages > MAX_TEXT_PAGES:
        a.limitations.append(f"Odczytaliśmy pierwsze {MAX_TEXT_PAGES} z {pages} stron.")
    if len("".join(text.split())) >= MIN_TEXT_CHARS:
        a.text = text
        return a

    # A scan (or an image-only PDF): OCR the first pages.
    try:
        ocr_text, method = _ocr_pages(data, pages, ocr)
    except OcrUnavailable as e:
        a.limitations.append(f"Dokument nie ma warstwy tekstowej (to skan), a odczyt tekstu z obrazu nie powiódł się. {e.message}")
        a.text = text
        return a
    except Exception as e:  # noqa: BLE001 - a malformed PDF page must not sink the check
        log.info("pdf render failed: %r", e)
        a.limitations.append("Nie udało się odczytać stron dokumentu jako obrazów.")
        a.text = text
        return a
    a.method = method or "pdf_ocr"
    a.text = ocr_text or text
    note = "Dokument nie ma warstwy tekstowej (to skan). Tekst odczytano automatycznie i może zawierać błędy."
    if pages > OCR_PAGES:
        note += f" Odczytaliśmy pierwsze {OCR_PAGES} z {pages} stron."
    a.limitations.append(note)
    if not a.text.strip():
        a.limitations.append("W dokumencie nie znaleźliśmy czytelnego tekstu.")
    return a
