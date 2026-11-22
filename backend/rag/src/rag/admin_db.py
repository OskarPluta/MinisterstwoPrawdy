"""Database operations of the admin panel (backend/admin). SQL is in database/queries/.

Nothing here deletes evidence: disabling a source and withdrawing a record only take them
out of search (and bump the corpus version, so running retrievers reload). The check log
keeps only masked user text; the admin panel never sees more.
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlsplit

from .database import query
from .db import Connection, _bump_corpus_version, upsert_source
from .embed import _fold
from .timeutil import utcnow_iso

SOURCE_TYPES = ("official", "structured", "factcheck", "secondary")
_DOMAIN = re.compile(r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
_SOURCE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,80}$")


# ---- audit -----------------------------------------------------------------------------

def audit(conn: Connection, action: str, target: str | None, details: dict[str, Any] | None = None) -> None:
    conn.execute(query("audit.insert"), {
        "at": utcnow_iso(), "action": action, "target": target,
        "details_json": json.dumps(details or {}, ensure_ascii=False, default=str),
    })


def audit_list(conn: Connection, limit: int = 200) -> list[dict[str, Any]]:
    rows = conn.execute(query("audit.list"), {"limit": limit}).fetchall()
    return [{**{k: v for k, v in r.items() if k != "details_json"}, "details": json.loads(r["details_json"])}
            for r in rows]


# ---- check log ---------------------------------------------------------------------------

def checks_page(
    conn: Connection, *, limit: int = 50, cursor: str | None = None, since: str | None = None,
    until: str | None = None, channel: str | None = None, status: str | None = None,
    outcomes: list[str] | None = None, q: str | None = None, unreviewed: bool = False,
) -> tuple[list[dict[str, Any]], str | None]:
    """One page of the check log, newest first; the cursor of the next page or None."""
    before_created, before_id = (cursor.split("|", 1) if cursor and "|" in cursor else (None, None))
    rows = conn.execute(query("checks.page"), {
        "before_created": before_created, "before_id": before_id, "since": since, "until": until,
        "channel": channel, "status": status, "outcomes": outcomes or None, "q": q or None,
        "unreviewed": unreviewed, "limit": limit + 1,
    }).fetchall()
    more = len(rows) > limit
    rows = rows[:limit]
    nxt = f"{rows[-1]['created_at']}|{rows[-1]['check_id']}" if more and rows else None
    return [dict(r) for r in rows], nxt


def get_check(conn: Connection, check_id: str) -> dict[str, Any] | None:
    row = conn.execute(query("checks.get"), {"check_id": check_id}).fetchone()
    if row is None:
        return None
    out = dict(row)
    out["result"] = json.loads(out.pop("result_json"))
    return out


def set_review(conn: Connection, check_id: str, label: str, note: str | None) -> None:
    conn.execute(query("checks.review_upsert"), {
        "check_id": check_id, "label": label, "note": note, "reviewed_at": utcnow_iso(),
    })


def delete_review(conn: Connection, check_id: str) -> None:
    conn.execute(query("checks.review_delete"), {"check_id": check_id})


def checks_in_range(conn: Connection, since: str, until: str | None = None) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(query("checks.in_range"), {"since": since, "until": until})]


def usage_calls_in_range(conn: Connection, since: str, until: str | None = None) -> list[dict[str, Any]]:
    """Per-call token counts (extract / verify / transcribe) of checks in the range."""
    out: list[dict[str, Any]] = []
    for r in conn.execute(query("checks.usage_by_call"), {"since": since, "until": until}):
        usage = (json.loads(r["result_json"]).get("usage") or {})
        for c in usage.get("by_call") or ():
            out.append({"created_at": r["created_at"], "model": r["model"], **c})
    return out


# ---- sources -----------------------------------------------------------------------------

def admin_sources(conn: Connection) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(query("sources.admin_list"))]


def get_source(conn: Connection, source_id: str) -> dict[str, Any] | None:
    row = conn.execute(query("sources.get"), {"source_id": source_id}).fetchone()
    return dict(row) if row else None


def update_source(
    conn: Connection, source_id: str, *, enabled: bool, publisher: str | None,
    source_type: str | None, note: str | None,
) -> dict[str, Any]:
    """Admin overrides; empty values clear them (back to what ingest wrote). Returns the
    previous row for the audit log."""
    if source_type is not None and source_type not in SOURCE_TYPES:
        raise ValueError(f"source_type: one of {', '.join(SOURCE_TYPES)}")
    before = get_source(conn, source_id)
    if before is None:
        raise KeyError(source_id)
    with conn.transaction():
        conn.execute(query("sources.admin_update"), {
            "source_id": source_id, "enabled": enabled,
            "admin_publisher": (publisher or "").strip() or None,
            "admin_source_type": source_type or None,
            "admin_note": (note or "").strip() or None, "at": utcnow_iso(),
        })
        _bump_corpus_version(conn)  # retrievers reload the dense index; FTS reads `enabled` directly
    return before


def normalize_domain(value: str) -> str:
    """`https://www.Example.pl/a` -> `example.pl`; ValueError if it is not a host name."""
    v = (value or "").strip().lower()
    host = urlsplit(v if "//" in v else f"//{v}").hostname or ""
    host = host.removeprefix("www.")
    if not _DOMAIN.match(host):
        raise ValueError("niepoprawna domena")
    return host


def create_source(
    conn: Connection, *, source_id: str, publisher: str, domain: str, source_type: str,
    note: str | None = None,
) -> dict[str, Any]:
    """A source added by hand in the admin panel (`admin-<...>`); its records come from links
    the admin adds (backend/admin/links.py), checked against `domain` like any ingest."""
    if not _SOURCE_ID.match(source_id):
        raise ValueError("source_id: małe litery, cyfry, '.', '_' i '-'")
    if source_type not in SOURCE_TYPES:
        raise ValueError(f"source_type: one of {', '.join(SOURCE_TYPES)}")
    if not publisher.strip():
        raise ValueError("publisher: wymagany")
    if get_source(conn, source_id) is not None:
        raise ValueError("źródło o tym identyfikatorze już istnieje")
    domain = normalize_domain(domain)
    with conn.transaction():
        upsert_source(conn, {
            "source_id": source_id, "publisher": publisher.strip(), "publishing_domain": domain,
            "source_type": source_type, "citation_url": f"https://{domain}/", "terms_url": None,
            "refresh_policy": "dodane ręcznie (panel administracyjny)",
        })
        if note:
            conn.execute(query("sources.admin_update"), {
                "source_id": source_id, "enabled": True, "admin_publisher": None,
                "admin_source_type": None, "admin_note": note.strip(), "at": utcnow_iso(),
            })
    row = get_source(conn, source_id)
    assert row is not None
    return row


def source_documents(conn: Connection, source_id: str) -> list[dict[str, Any]]:
    rows = [dict(r) for r in conn.execute(query("evidence.by_source"), {"source_id": source_id})]
    rows.sort(key=lambda r: r["fetched_at"] or "", reverse=True)
    return rows


def get_evidence(conn: Connection, evidence_id: int) -> dict[str, Any] | None:
    row = conn.execute(query("evidence.get"), {"evidence_id": evidence_id}).fetchone()
    return dict(row) if row else None


def withdraw_evidence(conn: Connection, evidence_id: int, note: str | None) -> dict[str, Any] | None:
    """Take a record (every version of its identity) out of search. Rows are kept for citation
    traceability, and ingest will not bring the identity back (rag/db.py:upsert_evidence)."""
    ev = get_evidence(conn, evidence_id)
    if ev is None:
        return None
    with conn.transaction():
        ids = conn.execute(query("evidence.withdraw"), {
            "source_id": ev["source_id"], "upstream_id": ev["upstream_id"], "at": utcnow_iso(),
            "note": (note or "").strip() or None,
        }).fetchall()
        for r in ids:
            conn.execute(query("passages.unindex_evidence"), {"evidence_id": r["evidence_id"]})
        _bump_corpus_version(conn)
    return get_evidence(conn, evidence_id)


def restore_evidence(conn: Connection, evidence_id: int) -> dict[str, Any] | None:
    """Undo a withdrawal: the latest version becomes current and searchable again."""
    ev = get_evidence(conn, evidence_id)
    if ev is None or ev["withdrawn_at"] is None:
        return None
    identity = {"source_id": ev["source_id"], "upstream_id": ev["upstream_id"]}
    with conn.transaction():
        conn.execute(query("evidence.unwithdraw"), identity)
        latest = conn.execute(query("evidence.latest_version"), identity).fetchone()
        assert latest is not None
        conn.execute(query("evidence.make_current"), {"evidence_id": latest["evidence_id"]})
        for p in conn.execute(query("passages.for_reindex"), {"evidence_id": latest["evidence_id"]}).fetchall():
            conn.execute(query("passages.set_fts"), {
                "passage_id": p["passage_id"], "title_fts": _fold(p["title"]),
                "heading_fts": _fold(p["heading"] or ""), "text_fts": _fold(p["search_text"]),
            })
        _bump_corpus_version(conn)
    return get_evidence(conn, latest["evidence_id"])


# ---- trusted fact-check domains (live lookup) ----------------------------------------------

def list_trusted_domains(conn: Connection) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(query("trusted_domains.list"))]


def add_trusted_domain(conn: Connection, domain: str, note: str | None = None, enabled: bool = True) -> str:
    d = normalize_domain(domain)
    conn.execute(query("trusted_domains.upsert"), {
        "domain": d, "enabled": enabled, "note": (note or "").strip() or None, "at": utcnow_iso(),
    })
    return d


def delete_trusted_domain(conn: Connection, domain: str) -> bool:
    return conn.execute(query("trusted_domains.delete"), {"domain": domain.strip().lower()}).fetchone() is not None
