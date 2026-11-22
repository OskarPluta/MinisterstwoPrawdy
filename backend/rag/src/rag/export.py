"""Analyst export of the check log (table `checks`) to JSON and CSV.

JSON (`format: prawdowid-export`, `version: 1`):

    {"format": "prawdowid-export", "version": 1, "exported_at": "<UTC ISO>",
     "timezone": "Europe/Warsaw", "filters": {...}, "count": <checks>, "notice": "...",
     "checks": [{"check_ref", "checked_at", "checked_at_local", "status", "message",
                 "injection_detected", "shared", "corpus_version", "model", "embedding_model",
                 "duration_ms", "claims": [{"claim", "verdict", "verdict_pl", "downgraded_from",
                 "explanation", "strength": {"level", "level_pl", "reasons"},
                 "known_disinformation", "conflict", "notes",
                 "citations": [{"n", "role", "title", "publisher", "url", "date_label", "text",
                                "is_verbatim", "attribution"}]}]}]}

CSV: one row per claim (a check without claims gives one row with empty claim columns), the
columns in CSV_COLUMNS. Written as UTF-8 with a BOM (spreadsheets then read Polish letters
right; `bom=False` for tools that do not expect one), comma-separated, RFC 4180 quoting, CRLF.

Rules:
- Fields are copied from an allowlist, so internal diagnostics (shortlist_ids, invented_ids,
  live, error, passage_id) and any field added later never leave by accident.
- The user's message (`input_masked`) is not exported at all, only the claims extracted from it
  (masked at check time and masked again here). check_id is the user's deletion key, so it is
  replaced by `check_ref`, a domain-separated SHA-256 of it: stable across exports (rows of one
  check and repeated exports can be joined), but the key cannot be recovered from it.
- CSV cells are untrusted text (model and user derived): a cell starting with = + - @ tab or CR
  (or their full-width forms) gets a leading apostrophe, so spreadsheets never run it as a formula.
- Times are stored in UTC and compared in Python; the export gives UTC and Europe/Warsaw.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .database import query
from .timeutil import WARSAW, parse_ts, utcnow_iso
from .verify import VERDICT_PL, mask

log = logging.getLogger(__name__)

EXPORT_FORMAT = "prawdowid-export"
EXPORT_VERSION = 1
STATUSES = ("ok", "needs_clarification", "out_of_scope", "too_long", "error")
VERDICTS = tuple(VERDICT_PL)
NOTICE = (
    "Log sprawdzeń z zamaskowanymi danymi osobowymi; bez treści wiadomości użytkownika i bez kluczy "
    "usunięcia (check_ref to skrót check_id). Cytaty ≤ 400 znaków z linkiem do źródła; licencje i "
    "wymagane oznaczenia źródeł: GET /api/sources. Retencja logu: docs/DANE_I_LICENCJE.md."
)
LIST_SEP = " | "  # joins list values inside one CSV cell

_CHECK_REF_SALT = b"prawdowid-export:check_ref:v1:"
_CITATION_FIELDS = ("n", "role", "title", "publisher", "url", "date_label", "text", "is_verbatim", "attribution")
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r", "＝", "＋", "－", "＠")

CSV_COLUMNS = (
    "check_ref", "checked_at_warsaw", "checked_at_utc", "status", "claim_no", "claims_in_check",
    "claim", "verdict", "verdict_pl", "downgraded_from", "strength", "strength_pl", "strength_reasons",
    "known_disinformation", "conflict", "explanation", "notes", "citation_count", "citations_supports",
    "citations_contradicts", "citation_urls", "publishers", "message", "injection_detected", "shared",
    "model", "embedding_model", "corpus_version", "duration_ms",
)


@dataclass
class ExportFilters:
    """since: inclusive, until: exclusive (aware datetimes). statuses / verdicts: empty = all.
    A verdict filter keeps only the matching claims and drops checks left without one."""

    since: datetime | None = None
    until: datetime | None = None
    statuses: tuple[str, ...] = field(default_factory=tuple)
    verdicts: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        bad = [s for s in self.statuses if s not in STATUSES]
        if bad:
            raise ValueError(f"unknown status {bad[0]!r} (expected one of {', '.join(STATUSES)})")
        bad = [v for v in self.verdicts if v not in VERDICTS]
        if bad:
            raise ValueError(f"unknown verdict {bad[0]!r} (expected one of {', '.join(VERDICTS)})")
        for name in ("since", "until"):
            t = getattr(self, name)
            if t is not None and t.tzinfo is None:
                raise ValueError(f"{name} must be timezone-aware")

    @classmethod
    def parse(
        cls, since: str | None = None, until: str | None = None,
        statuses: Iterable[str] = (), verdicts: Iterable[str] = (),
    ) -> ExportFilters:
        """From user input: ISO 8601 times (without an offset read in Europe/Warsaw, a date alone
        means its midnight); statuses / verdicts may also be comma-separated."""
        times: dict[str, datetime | None] = {}
        for name, raw in (("since", since), ("until", until)):
            times[name] = parse_ts(raw, naive_tz=WARSAW) if raw else None
            if raw and times[name] is None:
                raise ValueError(f"{name}: not an ISO 8601 time: {raw!r}")
        return cls(times["since"], times["until"], _split(statuses), _split(verdicts))

    def as_dict(self) -> dict[str, Any]:
        return {
            "since": _iso_utc(self.since), "until": _iso_utc(self.until),
            "status": list(self.statuses) or None, "verdict": list(self.verdicts) or None,
        }


def _split(values: Iterable[str]) -> tuple[str, ...]:
    out = [p.strip() for v in values for p in v.split(",") if p.strip()]
    return tuple(dict.fromkeys(out))


def _iso_utc(t: datetime | None) -> str | None:
    if t is None:
        return None
    return t.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def check_ref(check_id: str) -> str:
    """Pseudonymous, non-reversible reference to a check (check_id is the deletion key)."""
    return hashlib.sha256(_CHECK_REF_SALT + check_id.encode("utf-8")).hexdigest()[:16]


def _masked(text: Any) -> Any:
    return mask(text) if isinstance(text, str) else text


def _claim(c: dict[str, Any]) -> dict[str, Any]:
    s = c.get("strength")
    return {
        "claim": _masked(c.get("claim")),
        "verdict": c.get("verdict"),
        "verdict_pl": c.get("verdict_pl"),
        "downgraded_from": c.get("downgraded_from"),
        "explanation": _masked(c.get("explanation")),
        "strength": {"level": s.get("level"), "level_pl": s.get("level_pl"), "reasons": list(s.get("reasons") or [])}
        if isinstance(s, dict) else None,
        "known_disinformation": bool(c.get("known_disinformation")),
        "conflict": bool(c.get("conflict")),
        "notes": list(c.get("notes") or []),
        "citations": [{k: cit.get(k) for k in _CITATION_FIELDS} for cit in c.get("citations") or []],
    }


def export_record(row: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    """One check log row (+ its parsed result_json) as an export record (allowlisted fields)."""
    t = parse_ts(row.get("created_at"), naive_tz=timezone.utc)
    return {
        "check_ref": check_ref(str(row["check_id"])),
        "checked_at": _iso_utc(t),
        "checked_at_local": t.astimezone(WARSAW).replace(microsecond=0).isoformat() if t else None,
        "status": row.get("status"),
        "message": _masked(result.get("message")),
        "injection_detected": bool(result.get("injection_detected")),
        "shared": bool(row.get("shared")),
        "corpus_version": row.get("corpus_version"),
        "model": row.get("model"),
        "embedding_model": row.get("embedding_model"),
        "duration_ms": row.get("duration_ms"),
        "claims": [_claim(c) for c in result.get("claims") or []],
    }


def select(rows: Iterable[dict[str, Any]], filters: ExportFilters) -> list[dict[str, Any]]:
    """Filter check log rows in Python (times compared as aware UTC) and build export records,
    oldest first. Rows with an unreadable time are kept only when no time filter is set."""
    picked: list[tuple[datetime, dict[str, Any]]] = []
    oldest = datetime.min.replace(tzinfo=timezone.utc)
    for row in rows:
        t = parse_ts(row.get("created_at"), naive_tz=timezone.utc)
        if (filters.since or filters.until) and t is None:
            continue
        if t is not None and ((filters.since and t < filters.since) or (filters.until and t >= filters.until)):
            continue
        if filters.statuses and row.get("status") not in filters.statuses:
            continue
        try:
            result = json.loads(row.get("result_json") or "{}")
        except ValueError:
            log.warning("check log row with unreadable result_json skipped in export")
            continue
        rec = export_record(row, result)
        if filters.verdicts:
            rec["claims"] = [c for c in rec["claims"] if c["verdict"] in filters.verdicts]
            if not rec["claims"]:
                continue
        picked.append((t or oldest, rec))
    picked.sort(key=lambda p: (p[0], p[1]["check_ref"]))
    return [rec for _, rec in picked]


def load(conn: Any, filters: ExportFilters) -> list[dict[str, Any]]:
    """Export records from the check log of `conn` (a rag.db connection)."""
    return select(conn.execute(query("checks.export")), filters)


def to_json(records: list[dict[str, Any]], filters: ExportFilters | None = None,
            exported_at: str | None = None) -> str:
    doc = {
        "format": EXPORT_FORMAT,
        "version": EXPORT_VERSION,
        "exported_at": exported_at or utcnow_iso(),
        "timezone": "Europe/Warsaw",
        "filters": (filters or ExportFilters()).as_dict(),
        "count": len(records),
        "notice": NOTICE,
        "checks": records,
    }
    return json.dumps(doc, ensure_ascii=False, indent=2) + "\n"


def safe_cell(value: Any) -> Any:
    """CSV cell value: text that a spreadsheet could read as a formula gets a leading apostrophe.
    Booleans become true/false, None an empty cell; numbers stay numbers."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return value
    text = str(value)
    return "'" + text if text.startswith(_FORMULA_START) else text


def _join(values: Iterable[Any]) -> str:
    return LIST_SEP.join(str(v) for v in values if v not in (None, ""))


def csv_rows(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flat rows (before cell neutralisation), one per claim."""
    out: list[dict[str, Any]] = []
    for rec in records:
        t = parse_ts(rec["checked_at"])
        base = {
            "check_ref": rec["check_ref"],
            "checked_at_warsaw": t.astimezone(WARSAW).strftime("%Y-%m-%d %H:%M:%S") if t else None,
            "checked_at_utc": rec["checked_at"],
            "status": rec["status"],
            "claims_in_check": len(rec["claims"]),
            "message": rec["message"],
            "injection_detected": rec["injection_detected"],
            "shared": rec["shared"],
            "model": rec["model"],
            "embedding_model": rec["embedding_model"],
            "corpus_version": rec["corpus_version"],
            "duration_ms": rec["duration_ms"],
        }
        if not rec["claims"]:
            out.append(base)
        for i, c in enumerate(rec["claims"], 1):
            cits = c["citations"]
            s = c["strength"] or {}
            out.append({
                **base,
                "claim_no": i,
                "claim": c["claim"],
                "verdict": c["verdict"],
                "verdict_pl": c["verdict_pl"],
                "downgraded_from": c["downgraded_from"],
                "strength": s.get("level"),
                "strength_pl": s.get("level_pl"),
                "strength_reasons": _join(s.get("reasons") or []),
                "known_disinformation": c["known_disinformation"],
                "conflict": c["conflict"],
                "explanation": c["explanation"],
                "notes": _join(c["notes"]),
                "citation_count": len(cits),
                "citations_supports": sum(x["role"] == "supports" for x in cits),
                "citations_contradicts": sum(x["role"] == "contradicts" for x in cits),
                "citation_urls": _join(x["url"] for x in cits),
                "publishers": _join(dict.fromkeys(x["publisher"] for x in cits)),
            })
    return out


def to_csv(records: list[dict[str, Any]], bom: bool = True) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=CSV_COLUMNS, restval="", quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    w.writeheader()
    for row in csv_rows(records):
        w.writerow({k: safe_cell(v) for k, v in row.items()})
    return ("﻿" if bom else "") + buf.getvalue()


def filename(fmt: str, now: datetime | None = None) -> str:
    """prawdowid-export-<YYYY-MM-DD in Warsaw>.<fmt>"""
    day = (now or datetime.now(timezone.utc)).astimezone(WARSAW).strftime("%Y-%m-%d")
    return f"prawdowid-export-{day}.{fmt}"
