"""Links: find them in the user's text, fetch once, extract the page (or PDF, or image)."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from urllib.parse import urlsplit

from crawler.extract import extract_article

from rag.attachments import Attachment
from rag.timeutil import fmt_local

from .fetch import FetchFailed, SafeFetcher
from .image import read_image, sniff_image
from .ocr import Ocr
from .pdf import read_pdf

MAX_URLS = 2
ACCEPT = "text/html,application/xhtml+xml,application/pdf;q=0.9,image/*;q=0.8"
MIN_PAGE_CHARS = 80

_URL_RX = re.compile(r"(?:https?://|www\.)[^\s<>\"'„”«»]+", re.IGNORECASE)
_TRAILING = ".,;:!?)]}…"

def find_urls(text: str, limit: int = MAX_URLS) -> list[str]:
    out: list[str] = []
    for m in _URL_RX.finditer(text):
        u = m.group(0).rstrip(_TRAILING)
        if u.lower().startswith("www."):
            u = "https://" + u
        if u not in out:
            out.append(u)
    return out[:limit]


def domain_of(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def in_catalog(host: str, domains: set[str]) -> str | None:
    """The catalogue domain `host` belongs to (itself or a subdomain), or None."""
    return next((d for d in domains if host == d or host.endswith("." + d)), None)


def read_url(url: str, fetcher: SafeFetcher | None, ocr: Ocr,
             known_domains: Callable[[], set[str]] = set) -> Attachment:
    a = Attachment(kind="url", label=url)
    if fetcher is None:
        a.limitations.append(
            "Pobieranie stron nie jest skonfigurowane w tej instalacji (brak maila kontaktowego). "
            "Wklej treść strony albo prześlij zrzut ekranu."
        )
        return a
    try:
        r = fetcher.get(url, accept=ACCEPT)
    except FetchFailed as e:
        a.limitations.append(f"Nie udało się pobrać strony. {e.message}")
        return a

    host = domain_of(r.final_url)
    if domain_of(r.url) != host:
        a.notes.append(f"Link przekierował na inny adres: {host}.")
    if d := in_catalog(host, known_domains()):
        # A neutral fact, never grounds for a verdict (the task forbids judging by domain).
        a.notes.append(
            f"Adres należy do domeny z naszego katalogu źródeł ({d}). Mimo to wynik opiera się na naszej bazie, "
            "a nie na tej stronie."
        )

    if r.content_type == "application/pdf" or r.body[:5] == b"%PDF-":
        if r.truncated:
            a.limitations.append("Dokument jest za duży, żeby go pobrać.")
            return a
        pdf = read_pdf(r.body, url, ocr)
        pdf.notes[:0] = a.notes
        pdf.meta["final_url"] = r.final_url
        return pdf
    if r.content_type.startswith("image/") or (not r.is_html and sniff_image(r.body)):
        if r.truncated:
            a.limitations.append("Obraz jest za duży, żeby go pobrać.")
            return a
        img = read_image(r.body, url, ocr)
        img.notes[:0] = a.notes
        img.meta["final_url"] = r.final_url
        return img
    if not r.is_html:
        a.limitations.append(f"Tego rodzaju treści nie obsługujemy ({r.content_type or 'nieznany typ'}).")
        return a

    ex = extract_article(r.body, r.final_url)
    a.meta.update(sha256=hashlib.sha256(r.body).hexdigest(), mime=r.content_type, final_url=r.final_url,
                  extraction=ex.method)
    a.method = "html"
    if r.truncated:
        a.limitations.append("Strona jest bardzo duża. Odczytaliśmy tylko jej początek.")
    if len(ex.text.strip()) < MIN_PAGE_CHARS:
        a.limitations.append(
            "Nie udało się wyodrębnić treści strony (może wymagać JavaScriptu albo logowania). "
            "Wklej jej treść albo prześlij zrzut ekranu."
        )
        return a
    a.text = (f"Tytuł strony: {ex.title}\n\n" if ex.title else "") + ex.text
    if ex.date:
        a.material_date, a.material_date_kind = ex.date.isoformat(), "published"
        how = " (odczytana z treści strony, może być niedokładna)" if ex.date_guessed else ""
        a.notes.append(f"Strona podaje datę publikacji {fmt_local(ex.date.isoformat(), with_time=False)}{how}.")
    else:
        a.notes.append("Strona nie podaje daty publikacji.")
    return a
