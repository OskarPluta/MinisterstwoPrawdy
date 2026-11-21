"""Convert crawler output (data/documents.jsonl + crawl_log.jsonl) into the corpus format.

Crawler records are flat: {url, source, institution, doc_type, region, title, content,
published_at, fetched_at, ...}. Articles become one-section `article`/`report` records.
IMGW warnings arrive as "key: value" dumps and are mapped to typed `warning_meteo` /
`warning_hydro` fields so the fixed templates in chunking.py can render them.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .timeutil import WARSAW, parse_ts

ARTICLE_KINDS = {"komunikat": "article", "ostrzezenie": "article", "informacja": "article", "raport": "report"}
STRUCTURED_SOURCE_TYPES = {"ostrzezenie_meteo", "ostrzezenie_hydro"}
# Crawl-run statuses that mean the source was reached successfully (crawler SPEC §5);
# `not_modified` is a healthy run that found nothing new.
SUCCESS_STATUSES = {"ok", "not_modified"}

# Voivodeship by the first two TERYT digits.
TERYT_WOJ = {
    "02": "dolnośląskie", "04": "kujawsko-pomorskie", "06": "lubelskie", "08": "lubuskie",
    "10": "łódzkie", "12": "małopolskie", "14": "mazowieckie", "16": "opolskie",
    "18": "podkarpackie", "20": "podlaskie", "22": "pomorskie", "24": "śląskie",
    "26": "świętokrzyskie", "28": "warmińsko-mazurskie", "30": "wielkopolskie", "32": "zachodniopomorskie",
}
_UNTIL_REVOKED_YEAR = 9999


def _utc(value: str | None, naive_tz=WARSAW) -> str | None:
    """Crawler/IMGW times without an offset are Warsaw local. Returns UTC ISO 'Z' or None."""
    dt = parse_ts(value, naive_tz=naive_tz)
    if dt is None or dt.year >= _UNTIL_REVOKED_YEAR:
        return None
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _kv(content: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in content.splitlines():
        key, sep, val = line.partition(": ")
        if sep and re.fullmatch(r"[\wąćęłńóśźżĄĆĘŁŃÓŚŹŻ\[\].]+", key):
            out.setdefault(key, val.strip())
    return out


def _int(v: str | None) -> int | None:
    try:
        return int(v) if v not in (None, "") else None
    except ValueError:
        return None


def _paragraphs(content: str, title: str) -> str:
    """One paragraph per non-empty line; drop a leading line that repeats the title."""
    lines = [ln.strip() for ln in content.splitlines() if ln.strip()]
    if lines and title and lines[0].casefold() == title.strip().casefold():
        lines = lines[1:]
    return "\n\n".join(lines)


def _publishing_domain(host: str) -> str:
    return host.removeprefix("www.")


def _meteo(doc: dict[str, Any], base: dict[str, Any]) -> dict[str, Any]:
    kv = _kv(doc["content"])
    teryt = [c.strip() for c in kv.get("teryt", "").split(",") if c.strip()]
    woj = sorted({TERYT_WOJ.get(c[:2], f"TERYT {c[:2]}") for c in teryt})
    by_woj = Counter(TERYT_WOJ.get(c[:2]) for c in teryt)
    areas = [f"województwo {w} ({by_woj[w]} powiatów)" for w in woj] or ([doc["region"]] if doc.get("region") else [])
    desc = kv.get("tresc", "")
    if kv.get("komentarz") and kv["komentarz"].rstrip(".") != "Brak":
        desc += f" Komentarz: {kv['komentarz']}"
    return {
        **base,
        "kind": "warning_meteo",
        "upstream_id": kv.get("id") or doc["url"],
        "published_at": _utc(kv.get("opublikowano")) or base["published_at"],
        "valid_from": _utc(kv.get("obowiazuje_od")),
        "valid_to": _utc(kv.get("obowiazuje_do")),
        "areas": areas,
        "fields": {
            "issuer": "IMGW-PIB", "office": kv.get("biuro"),
            "phenomenon": kv.get("nazwa_zdarzenia"), "level": _int(kv.get("stopien")),
            "probability": _int(kv.get("prawdopodobienstwo")), "description": desc.strip() or None,
            "teryt": teryt, "attribution": doc.get("attribution"),
        },
    }


def _hydro(doc: dict[str, Any], base: dict[str, Any]) -> dict[str, Any]:
    kv = _kv(doc["content"])
    areas = [v for k, v in kv.items() if re.fullmatch(r"obszary\[\d+\]\.opis", k)]
    valid_to_raw = kv.get("data_do")
    until_revoked = bool(valid_to_raw and valid_to_raw.startswith(str(_UNTIL_REVOKED_YEAR)))
    return {
        **base,
        "kind": "warning_hydro",
        "published_at": _utc(kv.get("opublikowano")) or base["published_at"],
        "valid_from": _utc(kv.get("data_od")),
        "valid_to": _utc(valid_to_raw),
        "status": "obowiązuje do odwołania" if until_revoked else None,
        "areas": areas or ([doc["region"]] if doc.get("region") else []),
        "fields": {
            "issuer": "IMGW-PIB", "office": kv.get("biuro"), "number": _int(kv.get("numer")),
            "phenomenon": kv.get("zdarzenie"), "level": _int(kv.get("stopień")),
            "probability": _int(kv.get("prawdopodobienstwo")),
            "description": " ".join(
                x for x in (kv.get("przebieg"), kv.get("komentarz")) if x and x.rstrip(".") != "Brak"
            ) or None,
            "attribution": doc.get("attribution"),
        },
    }


def convert_document(doc: dict[str, Any]) -> dict[str, Any]:
    base: dict[str, Any] = {
        "source_id": doc["source"],
        "upstream_id": doc["url"],
        "title": doc.get("title") or doc["url"],
        "citation_url": doc["url"],
        "published_at": _utc(doc.get("published_at")),
        "fetched_at": _utc(doc.get("fetched_at"), naive_tz=timezone.utc),
        "areas": [doc["region"]] if doc.get("region") else [],
    }
    dtype = doc.get("doc_type")
    if dtype == "ostrzezenie_meteo":
        return _meteo(doc, base)
    if dtype == "ostrzezenie_hydro":
        return _hydro(doc, base)
    rec = {**base, "kind": ARTICLE_KINDS.get(dtype, "article"),
           "sections": [{"heading": None, "text": _paragraphs(doc["content"], doc.get("title") or "")}]}
    if doc.get("attribution"):
        rec["fields"] = {"attribution": doc["attribution"]}
    return rec


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Parse JSONL; skip a truncated last line (the crawler may be appending right now)."""
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    out = []
    for i, ln in enumerate(lines):
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            if i == len(lines) - 1:
                break
            raise
    return out


def convert(documents: Path, crawl_log: Path | None = None) -> dict[str, Any]:
    docs = _read_jsonl(documents)
    runs: dict[str, dict[str, Any]] = {}
    last_success: dict[str, str | None] = {}
    if crawl_log and crawl_log.exists():
        for run in _read_jsonl(crawl_log):
            runs[run["source"]] = run  # last run per source wins
            if run.get("status") in SUCCESS_STATUSES:
                last_success[run["source"]] = _utc(run.get("finished_at"), naive_tz=timezone.utc)

    sources: dict[str, dict[str, Any]] = {}
    for doc in docs:
        sid = doc["source"]
        if sid in sources:
            continue
        host = (urlparse(doc["url"]).hostname or "").lower()
        listing = f"{urlparse(doc['url']).scheme}://{host}/"
        src: dict[str, Any] = {
            "source_id": sid,
            "publisher": doc.get("institution") or sid,
            "publishing_domain": _publishing_domain(host),
            "source_type": "structured" if doc.get("doc_type") in STRUCTURED_SOURCE_TYPES else "official",
            "citation_url": listing,
            "terms_url": None,
            "refresh_policy": None,
        }
        run = runs.get(sid)
        if run:
            ok = run.get("status") in SUCCESS_STATUSES
            src["last_success_at"] = last_success.get(sid)  # an earlier success survives a later failure
            src["last_error"] = None if ok else f"{run.get('status')}: {run.get('message')}"
        sources[sid] = src

    # Records for sources that never produced a document are not representable; report them.
    return {
        "_comment": f"Converted from {documents.name} on {datetime.now(timezone.utc):%Y-%m-%dT%H:%MZ}.",
        "sources": list(sources.values()),
        "evidence": [convert_document(d) for d in docs],
        "_crawl_without_documents": sorted(set(runs) - set(sources)),
    }
