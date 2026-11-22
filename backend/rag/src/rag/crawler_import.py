"""Convert crawler output (documents.jsonl + crawl_log.jsonl) into the corpus format.

Crawler records are flat: {url, source, institution, doc_type, region, title, content,
published_at, fetched_at, ...}. Articles become one-section `article`/`report` records.
IMGW warnings arrive as "key: value" dumps and are mapped to typed `warning_meteo` /
`warning_hydro` fields so the fixed templates in chunking.py can render them. Likewise IMGW SYNOP readings
become `weather_reading` and GIOŚ air-quality indices `air_quality`. RSO notices keep their own text as `alert`
(quoted verbatim, with a validity window).
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
STRUCTURED_SOURCE_TYPES = {"ostrzezenie_meteo", "ostrzezenie_hydro", "ostrzezenie_rso", "pomiar_synop",
                           "pomiar_powietrza"}
# Fact-check verdicts and disinformation cases (Google Fact Check, EUvsDisinfo): source type "factcheck",
# which verify.py searches for opinion-shaped claims and uses to mark known disinformation narratives.
# Only the source row changes, so existing records keep their content hash (no re-embedding).
FACTCHECK_DOC_TYPES = {"weryfikacja"}
# Crawl-run statuses that mean the source was reached successfully (statuses: backend/crawler/README.md);
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


# RSO keys as flattened by the crawler's json_api adapter; any other line continues the previous value
# (`content` spans several lines and its lines look like "Zjawisko: …").
_RSO_KEY = re.compile(
    r"id|title|shortcut|content|rso_alarm|rso_icon|valid_from|valid_to|created_at|updated_at|repetition|"
    r"longitude|latitude|water_level_\w+|river_name|location_name|provinces\.\d+\.(?:name|city)"
)


def _kv_multiline(content: str, is_key) -> dict[str, str]:
    """Like `_kv`, but a line whose key fails `is_key` is appended to the previous value."""
    out: dict[str, str] = {}
    last = None
    for line in content.splitlines():
        key, sep, val = line.partition(": ")
        if sep and is_key(key):
            last = key
            out.setdefault(key, val.strip())
        elif last and line.strip():
            out[last] += "\n" + line.strip()
    return out


def _float(v: str | None) -> float | None:
    try:
        return float(v) if v not in (None, "") else None
    except ValueError:
        return None


def _rso(doc: dict[str, Any], base: dict[str, Any]) -> dict[str, Any]:
    kv = _kv_multiline(doc["content"], lambda k: bool(_RSO_KEY.fullmatch(k)))
    title = kv.get("title") or base["title"]
    provinces = [v.lower() for k, v in kv.items() if k.endswith(".name") and v]
    cities = [v for k, v in kv.items() if k.endswith(".city") and v]
    return {
        **base,
        "kind": "alert",
        "title": title,
        "published_at": _utc(kv.get("created_at")) or base["published_at"],
        "valid_from": _utc(kv.get("valid_from")),
        "valid_to": _utc(kv.get("valid_to")),
        "areas": [f"województwo {p}" for p in provinces] + cities,
        "sections": [{"heading": None, "text": _paragraphs(kv.get("content") or title, title)}],
    }


def _synop(doc: dict[str, Any], base: dict[str, Any]) -> dict[str, Any]:
    kv = _kv(doc["content"])
    day, hour = kv.get("data_pomiaru"), _int(kv.get("godzina_pomiaru"))
    observed = _utc(f"{day}T{hour:02d}:00:00", naive_tz=timezone.utc) if day and hour is not None else None
    return {
        **base,
        "kind": "weather_reading",
        "published_at": observed or base["published_at"],
        "observed_at": observed,
        "areas": [kv["stacja"]] if kv.get("stacja") else [],
        "fields": {
            "issuer": "IMGW-PIB", "station": kv.get("stacja"), "station_id": kv.get("id_stacji"),
            "temperature": _float(kv.get("temperatura")), "wind_speed": _float(kv.get("predkosc_wiatru")),
            "wind_direction": _int(kv.get("kierunek_wiatru")), "humidity": _float(kv.get("wilgotnosc_wzgledna")),
            "precipitation": _float(kv.get("suma_opadu")), "pressure": _float(kv.get("cisnienie")),
            "attribution": doc.get("attribution"),
        },
    }


def _gios(doc: dict[str, Any], base: dict[str, Any]) -> dict[str, Any]:
    kv = _kv_multiline(doc["content"], lambda k: True)  # keys contain spaces ("Nazwa miasta")
    city, woj = kv.get("Nazwa miasta"), (kv.get("Województwo") or "").lower() or None
    # "indeks.Nazwa kategorii indeksu dla wskażnika PM10" (GIOŚ spells it both wskaźnika and wskażnika)
    pollutants = {m.group(1): v.lower() for k, v in kv.items()
                  if v and (m := re.fullmatch(r"indeks\.Nazwa kategorii indeksu dla wska[zż]nika (.+)", k))}
    index = kv.get("indeks.Nazwa kategorii indeksu")
    observed = _utc(kv.get("indeks.Data wykonania obliczeń indeksu"))
    return {
        **base,
        "kind": "air_quality",
        "published_at": observed or base["published_at"],
        "observed_at": observed,
        "areas": [a for a in (city, f"województwo {woj}" if woj else None) if a],
        "fields": {
            "issuer": "GIOŚ", "station": kv.get("Nazwa stacji"), "station_id": kv.get("Identyfikator stacji"),
            "index": index.lower() if index else None, "pollutants": pollutants,
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
    if dtype == "ostrzezenie_rso":
        return _rso(doc, base)
    if dtype == "pomiar_synop":
        return _synop(doc, base)
    if dtype == "pomiar_powietrza":
        return _gios(doc, base)
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


def _source_type(doc_type: str | None) -> str:
    if doc_type in STRUCTURED_SOURCE_TYPES:
        return "structured"
    return "factcheck" if doc_type in FACTCHECK_DOC_TYPES else "official"


# Licence and credit of a crawler source, from backend/crawler/sources.yaml (rule `licenses`).
CATALOG_FIELDS = ("license", "terms_url", "attribution", "processed_notice")


def source_catalog(config_path: Path | None = None) -> dict[str, dict[str, Any]]:
    """Licence fields of every crawler source, keyed by source name (= source_id)."""
    from crawler.config import DEFAULT_CONFIG, load_config

    cfg = load_config(config_path or DEFAULT_CONFIG)
    return {s["name"]: {k: s.get(k) for k in CATALOG_FIELDS} for s in cfg.sources}


def convert(
    documents: Path, crawl_log: Path | None = None, catalog: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """`catalog` (source_catalog()) supplies licence and credit; sources missing from it keep None."""
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
            "source_type": _source_type(doc.get("doc_type")),
            "citation_url": listing,
            "refresh_policy": None,
            **{k: (catalog or {}).get(sid, {}).get(k) for k in CATALOG_FIELDS},
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
