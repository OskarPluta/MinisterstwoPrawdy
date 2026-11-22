"""Adding a single link (an article on a trusted source's domain) as evidence.

The page is fetched with intake's SafeFetcher: the crawler's rules (robots.txt on every
address and redirect, at least 3 s per host, an honest User-Agent with a contact address, no
working around blocks or CAPTCHAs) plus SSRF protection (public addresses, ports 80/443).
The record then goes through `ingest_corpus` like any other, so it is versioned, checked
against the source's publishing domain, and cited only by its `citation_url`.
"""

from __future__ import annotations

from datetime import timezone
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from rag.db import Connection
from rag.embed import Embedder
from rag.ingest import ingest_corpus
from rag.timeutil import parse_ts, utcnow_iso

MIN_TEXT_CHARS = 200


class LinkError(Exception):
    """`message` is shown to the admin (Polish)."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def _on_domain(url: str, domain: str) -> bool:
    u = urlsplit(url)
    host = (u.hostname or "").lower()
    domain = domain.lower()
    return u.scheme == "https" and (host == domain or host.endswith("." + domain))


def _utc(value: str) -> str | None:
    dt = parse_ts(value)
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z") if dt else None


def _canonical(url: str) -> str:
    u = urlsplit(url)
    return urlunsplit((u.scheme, (u.hostname or "").lower() + (f":{u.port}" if u.port else ""), u.path or "/",
                       u.query, ""))


def fetch_record(fetcher: Any, source: dict[str, Any], url: str) -> dict[str, Any]:
    """Fetch and extract the page; the evidence record (not stored yet). No database access."""
    from crawler.extract import extract_article
    from intake.fetch import FetchFailed

    domain = source["publishing_domain"]
    url = (url or "").strip()
    if not _on_domain(url, domain):
        raise LinkError(f"Adres musi zaczynać się od https:// i należeć do domeny źródła ({domain}).")
    if fetcher is None:
        raise LinkError("Pobieranie jest wyłączone: ustaw INTAKE_CONTACT albo CRAWLER_CONTACT (adres e-mail).")
    try:
        page = fetcher.get(url, accept="text/html,application/xhtml+xml")
    except FetchFailed as e:
        raise LinkError(e.message) from None
    final = _canonical(page.final_url)
    if not _on_domain(final, domain):
        raise LinkError(f"Strona przekierowała poza domenę źródła ({domain}).")
    if not page.is_html:
        raise LinkError("Pod tym adresem nie ma strony HTML.")
    ex = extract_article(page.body, final)
    text = (ex.text or "").strip()
    if len(text) < MIN_TEXT_CHARS:
        raise LinkError("Nie udało się wyodrębnić treści artykułu z tej strony.")
    title = (ex.title or final).strip()
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if lines and lines[0].casefold() == title.casefold():
        lines = lines[1:]
    return {
        "source_id": source["source_id"], "upstream_id": final, "kind": "article", "title": title,
        "citation_url": final, "fetched_at": utcnow_iso(),
        "published_at": _utc(ex.date.isoformat()) if ex.date else None,
        "sections": [{"heading": None, "text": "\n\n".join(lines)}],
    }


def store_record(conn: Connection, record: dict[str, Any], embedder: Embedder | None) -> dict[str, Any]:
    """Ingest the record (no catalog change: the source already exists)."""
    from database import query

    actions = ingest_corpus(conn, {"sources": [], "evidence": [record]}, embedder)
    if actions.get("invalid"):
        raise LinkError("Rekord odrzucony przy zapisie (sprawdź domenę źródła).")
    action = next(iter(actions), "unchanged")
    if action == "withdrawn":
        raise LinkError("Ten dokument został wcześniej wycofany. Przywróć go na liście dokumentów źródła.")
    row = conn.execute(query("evidence.find_current"),
                       {"source_id": record["source_id"], "upstream_id": record["upstream_id"]}).fetchone()
    return {"action": action, "evidence_id": row["evidence_id"] if row else None,
            "title": record["title"], "citation_url": record["citation_url"]}
