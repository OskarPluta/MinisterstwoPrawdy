"""Intake facade: one call turns the user's text and files into rag Attachments.

Used by every channel (web API today, a messenger adapter later), so the same material gives
the same attachments, and therefore the same result, whichever way it arrives.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass

from rag.attachments import MAX_ATTACHMENTS, Attachment

from .fetch import SafeFetcher, contact_from_env, user_agent
from .image import read_image, sniff_image
from .ocr import Transcriber, make_ocr
from .pdf import read_pdf
from .web import find_urls, read_url
from .youtube import read_youtube, video_id

log = logging.getLogger(__name__)

MAX_FILES = 3
MAX_FILE_BYTES = 15_000_000  # the PDF limit; images have their own, lower one


@dataclass
class Upload:
    filename: str
    data: bytes


def _safe_label(name: str) -> str:
    """A file name for display: no path, no control characters, bounded."""
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(ch for ch in name if ch.isprintable()).strip()
    return name[:120] or "plik"


class Intake:
    def __init__(self, fetcher: SafeFetcher | None, youtube_key: str | None = None, ocr_mode: str | None = None):
        self.fetcher = fetcher
        self.youtube_key = youtube_key
        self.ocr_mode = ocr_mode

    def read_file(self, up: Upload, transcriber: Transcriber | None) -> Attachment:
        label = _safe_label(up.filename)
        ocr = make_ocr(transcriber, self.ocr_mode)
        try:
            if up.data[:5] == b"%PDF-":
                return read_pdf(up.data, label, ocr)
            if sniff_image(up.data):
                return read_image(up.data, label, ocr)
        except Exception as e:  # noqa: BLE001 - one bad file must not sink the check
            log.warning("reading %s failed: %r", label, e)
            return Attachment(kind="pdf" if up.data[:5] == b"%PDF-" else "image", label=label,
                              limitations=["Nie udało się przetworzyć tego pliku."])
        return Attachment(kind="image", label=label,
                          limitations=["Ten rodzaj pliku nie jest obsługiwany. Prześlij zdjęcie, zrzut ekranu albo PDF."])

    def read_link(self, url: str, transcriber: Transcriber | None,
                  known_domains: Callable[[], set[str]] = set) -> Attachment:
        try:
            if vid := video_id(url):
                return read_youtube(url, vid, self.fetcher, self.youtube_key)
            return read_url(url, self.fetcher, make_ocr(transcriber, self.ocr_mode), known_domains)
        except Exception as e:  # noqa: BLE001
            log.warning("reading link failed: %r", e)
            return Attachment(kind="url", label=url, limitations=["Nie udało się przetworzyć tego adresu."])

    def collect(self, text: str, files: list[Upload] = (), transcriber: Transcriber | None = None,
                known_domains: Callable[[], set[str]] = set) -> list[Attachment]:
        """Links found in `text` first, then files; at most MAX_ATTACHMENTS in total."""
        out = [self.read_link(u, transcriber, known_domains) for u in find_urls(text)]
        out += [self.read_file(f, transcriber) for f in list(files)[:MAX_FILES]]
        return out[:MAX_ATTACHMENTS]


def make_intake() -> Intake:
    """From the environment: INTAKE_CONTACT (or CRAWLER_CONTACT), YOUTUBE_API_KEY, PRAWDOWID_OCR.
    Without a contact address links are not fetched (the user is told so), like the crawler."""
    contact = contact_from_env()
    if not contact:
        log.warning("INTAKE_CONTACT/CRAWLER_CONTACT not set: links from users will not be fetched")
    fetcher = SafeFetcher(user_agent(contact)) if contact else None
    return Intake(fetcher, youtube_key=os.environ.get("YOUTUBE_API_KEY") or None)
