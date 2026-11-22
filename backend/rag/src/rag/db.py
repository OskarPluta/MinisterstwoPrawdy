"""PostgreSQL storage: sources, versioned evidence, passages (+full-text, vectors), checks.

All SQL lives in the `database` package (database/: schema.sql, queries/*.sql) and is executed with
bound parameters only.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import statistics
import uuid
from collections import Counter
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

import numpy as np
import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from database import query, schema

from .config import database_url
from .embed import _fold
from .timeutil import parse_ts, utcnow_iso

SCHEMA_VERSION = 5  # v4: reprint_signatures (rag/reprints.py); v5: admin panel (admin-backend)

Connection = psycopg.Connection[dict[str, Any]]


def _one(cur: psycopg.Cursor[dict[str, Any]]) -> dict[str, Any]:
    """The single row of a statement that always returns one (RETURNING, aggregates)."""
    row = cur.fetchone()
    if row is None:
        raise RuntimeError("query returned no row")
    return row


def _identifier_sql(name: str, ident: str) -> sql.Composed:
    return sql.SQL(query(name)).format(schema=sql.Identifier(ident))


def connect(url: str | None = None, *, db_schema: str | None = None) -> Connection:
    """Open an autocommit connection (multi-statement writes use `conn.transaction()`)
    and create or check the schema. `db_schema` selects a Postgres schema other than
    the default search_path (created if missing); tests and evals use throwaway ones."""
    conn = Connection.connect(url or database_url(), autocommit=True, row_factory=dict_row)
    try:
        if db_schema:
            conn.execute(_identifier_sql("meta.create_schema", db_schema))
            conn.execute(query("meta.set_search_path"), {"schema": db_schema})
        with conn.transaction():
            conn.execute(query("meta.schema_lock"))
            found: int | None = None  # None: a new database gets the current schema.sql as is
            if _one(conn.execute(query("meta.has_meta")))["present"]:
                row = conn.execute(query("meta.get_schema_version")).fetchone()
                found = int(row["value"]) if row else 0
                if found > SCHEMA_VERSION:
                    raise RuntimeError(
                        f"database schema v{found} is newer than this code (v{SCHEMA_VERSION})"
                    )
            # Columns added to existing tables come before schema.sql, whose indexes may refer
            # to them; the other steps come after it.
            if found is not None and 1 <= found < 5:
                conn.execute(query("migrate.v5_columns"))
            conn.execute(schema())  # static DDL from schema.sql
            # Upgrade steps for databases created by an older version (found < version <= SCHEMA_VERSION).
            if found is not None and found < 2:
                conn.execute(query("meta.migrate_v2"))
            if found is not None and found < 3:
                conn.execute(query("meta.migrate_v3"))
            if found is not None and found < 4:
                # v4 adds reprint_signatures (created by schema.sql above); detect reprints
                # among the records already stored.
                from .reprints import refresh

                refresh(conn)
            if found is None or found < 5:
                _seed_trusted_domains(conn)
            if found is not None and found < 5:
                _backfill_check_outcomes(conn)
            conn.execute(query("meta.set_schema_version"), {"version": str(SCHEMA_VERSION)})
    except BaseException:
        conn.close()
        raise
    return conn


def _seed_trusted_domains(conn: Connection) -> None:
    """Once per database (schema v5): later the admin panel owns the list, deletions included."""
    from .trusted import DEFAULT_TRUSTED_FACTCHECK_DOMAINS

    at = utcnow_iso()
    for d in DEFAULT_TRUSTED_FACTCHECK_DOMAINS:
        conn.execute(query("trusted_domains.seed"), {"domain": d, "at": at})


def _backfill_check_outcomes(conn: Connection) -> None:
    """Schema v5: derive outcome, error type, tokens and cost of checks logged before v5."""
    from .outcomes import classify, usage_columns

    for row in conn.execute(query("migrate.checks_without_outcome")).fetchall():
        try:
            payload = json.loads(row["result_json"])
        except ValueError:
            payload = {"status": row["status"]}
        payload.setdefault("status", row["status"])
        outcome, error_type = classify(payload)
        conn.execute(query("checks.set_derived"), {
            "check_id": row["check_id"], "outcome": outcome, "error_type": error_type,
            **usage_columns(payload, row["model"]),
        })


@contextmanager
def scratch_db(url: str | None = None) -> Iterator[Connection]:
    """A connection to a new, uniquely named schema that is dropped on exit."""
    name = f"scratch_{uuid.uuid4().hex[:12]}"
    conn = connect(url, db_schema=name)
    try:
        yield conn
    finally:
        try:
            conn.execute(_identifier_sql("meta.drop_schema", name))
        finally:
            conn.close()


def corpus_version(conn: Connection) -> int:
    return _one(conn.execute(query("meta.get_corpus_version")))["value"]


def _bump_corpus_version(conn: Connection) -> None:
    conn.execute(query("meta.bump_corpus_version"))


_CATALOG_FIELDS = (
    "publisher", "publishing_domain", "source_type", "terms_url", "license", "attribution",
    "processed_notice", "citation_url", "refresh_policy",
)
_STATUS_FIELDS = ("last_success_at", "last_error")


def upsert_source(conn: Connection, src: dict[str, Any]) -> None:
    """Catalog fields are always written. Fetch status is written only when `src`
    carries it, so reloading the catalog keeps the last recorded success or error."""
    params = {c: src.get(c) for c in ("source_id", *_CATALOG_FIELDS, *_STATUS_FIELDS)}
    params.update({f"has_{f}": f in src for f in _STATUS_FIELDS})
    conn.execute(query("sources.upsert"), params)


def source_domains(conn: Connection) -> dict[str, str]:
    return {r["source_id"]: r["publishing_domain"] for r in conn.execute(query("sources.domains"))}


def list_sources(conn: Connection) -> list[dict[str, Any]]:
    """Source catalog with fetch status and the number of current records (admin overrides applied)."""
    return list(conn.execute(query("sources.list")))


def trusted_domains(conn: Connection) -> list[str]:
    """Enabled fact-check domains for live lookup (table `trusted_domains`, admin panel)."""
    return [r["domain"] for r in conn.execute(query("trusted_domains.enabled"))]


# Fields that define the content of an evidence record. Fetch time is excluded so
# re-fetching identical content does not create a new version.
_HASHED_FIELDS = (
    "kind", "title", "citation_url", "published_at", "observed_at", "valid_from",
    "valid_to", "areas", "status", "syndicated_from", "fields", "sections",
)


def content_hash(record: dict[str, Any]) -> str:
    payload = {k: record.get(k) for k in _HASHED_FIELDS}
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass
class PassageIn:
    ord: int
    heading: str | None
    text: str  # searchable body text (without title/heading)
    excerpt: str | None  # exact source text, None for rendered structured passages
    page: int | None = None
    embedding: np.ndarray | None = None


@dataclass
class UpsertResult:
    action: Literal["inserted", "new_version", "unchanged", "known_version", "withdrawn"]
    evidence_id: int
    version: int


def _vector_bytes(v: np.ndarray | None) -> bytes | None:
    return None if v is None else v.astype(np.float32).tobytes()


def find_by_hash(conn: Connection, record: dict[str, Any], h: str) -> UpsertResult | None:
    """The stored version with this content, if any. "known_version" means a replayed
    older snapshot, which must not resurrect it (a genuine upstream revert carries a
    new update time, so its hash differs)."""
    row = conn.execute(
        query("evidence.find_by_hash"),
        {"source_id": record["source_id"], "upstream_id": record["upstream_id"], "content_hash": h},
    ).fetchone()
    if row is None:
        return None
    action = "unchanged" if row["is_current"] else "withdrawn" if row["withdrawn_at"] else "known_version"
    return UpsertResult(action, row["evidence_id"], row["version"])


def upsert_evidence(
    conn: Connection,
    record: dict[str, Any],
    passages: list[PassageIn],
    embedding_model: str | None,
) -> UpsertResult:
    """Insert or version one evidence record with its passages, full-text entries and vectors.

    Runs in a single transaction. Identity is (source_id, upstream_id); a changed
    content hash creates a new version and retires the previous one from search.
    """
    h = content_hash(record)
    identity = {"source_id": record["source_id"], "upstream_id": record["upstream_id"]}
    with conn.transaction():
        # An identity withdrawn in the admin panel stays out, whatever its content now is (and
        # a fresh version 1 would clash with the kept rows).
        withdrawn = conn.execute(query("evidence.is_withdrawn"), identity).fetchone()
        if withdrawn is not None:
            return UpsertResult("withdrawn", withdrawn["evidence_id"], withdrawn["version"])
        found = find_by_hash(conn, record, h)
        if found is not None:
            return found
        cur = conn.execute(query("evidence.find_current"), identity).fetchone()
        version = 1 if cur is None else cur["version"] + 1
        new_id = _one(conn.execute(
            query("evidence.insert"),
            {
                **identity,
                "version": version,
                "content_hash": h,
                "kind": record["kind"],
                "title": record["title"],
                "citation_url": record["citation_url"],
                "published_at": record.get("published_at"),
                "fetched_at": record.get("fetched_at") or utcnow_iso(),
                "observed_at": record.get("observed_at"),
                "valid_from": record.get("valid_from"),
                "valid_to": record.get("valid_to"),
                "areas_json": json.dumps(record.get("areas") or [], ensure_ascii=False),
                "status": record.get("status"),
                "syndicated_from": record.get("syndicated_from"),
                "fields_json": json.dumps(record.get("fields") or {}, ensure_ascii=False),
            },
        ))["evidence_id"]

        if cur is not None:
            # Keep the old version and its passages for citation traceability,
            # but remove it from full-text search (dense search filters on is_current).
            conn.execute(query("evidence.retire"), {"new_id": new_id, "old_id": cur["evidence_id"]})
            conn.execute(query("passages.unindex_evidence"), {"evidence_id": cur["evidence_id"]})

        title_fts = _fold(record["title"])
        for p in passages:
            conn.execute(
                query("passages.insert"),
                {
                    "evidence_id": new_id,
                    "ord": p.ord,
                    "heading": p.heading,
                    "page": p.page,
                    "excerpt": p.excerpt,
                    "search_text": p.text,
                    "is_verbatim": p.excerpt is not None,
                    "embedding": _vector_bytes(p.embedding),
                    "embedding_model": embedding_model if p.embedding is not None else None,
                    "title_fts": title_fts,
                    "heading_fts": _fold(p.heading or ""),
                    "text_fts": _fold(p.text),
                },
            )
        _bump_corpus_version(conn)
    return UpsertResult("inserted" if cur is None else "new_version", new_id, version)


def set_embeddings(conn: Connection, items: Iterable[tuple[int, np.ndarray]], model: str) -> None:
    with conn.transaction(), conn.cursor() as cur:
        cur.executemany(
            query("passages.set_embedding"),
            [{"embedding": _vector_bytes(v), "model": model, "passage_id": pid} for pid, v in items],
        )
        _bump_corpus_version(conn)


def corpus_totals(conn: Connection) -> dict[str, Any]:
    """Counts before chunking (documents/records) plus passages."""
    return dict(conn.execute(query("meta.corpus_totals")).fetchone() or {})


# Columns of a `checks` row a caller may leave out (rows written before schema v5 had none).
_CHECK_OPTIONAL = (
    "channel", "outcome", "error_type", "llm_calls", "input_tokens", "output_tokens", "cost",
    "cost_currency",
)


def record_check(conn: Connection, row: dict[str, Any]) -> None:
    conn.execute(query("checks.insert"), {**dict.fromkeys(_CHECK_OPTIONAL), **row})


def checks_stats(conn: Connection) -> dict[str, Any]:
    """Aggregate observability metrics (latency, verdicts, errors, usage/cost) from `checks`."""
    rows = conn.execute(query("checks.list")).fetchall()
    by_status: Counter[str] = Counter()
    by_verdict: Counter[str] = Counter()
    durations: list[int] = []
    injection_detected = 0
    search_errors = 0
    live_lookups = 0
    calls = 0
    input_tokens = 0
    output_tokens = 0
    cost_usd = 0.0
    cost_by_currency: Counter[str] = Counter()

    for row in rows:
        status = row["status"]
        by_status[status] += 1
        if row["duration_ms"] is not None:
            durations.append(int(row["duration_ms"]))
        payload = json.loads(row["result_json"])
        if payload.get("injection_detected"):
            injection_detected += 1
        claims = payload.get("claims") or []
        if status == "error" and not any(c.get("verdict") == "error" for c in claims):
            search_errors += 1
        for c in claims:
            verdict = c.get("verdict")
            if verdict:
                by_verdict[verdict] += 1
            if verdict == "error":
                search_errors += 1
            live = c.get("live")
            if isinstance(live, dict):
                live_lookups += 1
                if live.get("error"):
                    search_errors += 1
                search_errors += sum(
                    1
                    for s in (live.get("sources") or ())
                    if s.get("error") or s.get("status") not in (None, "ok", "skipped")
                )
        usage = payload.get("usage")
        if isinstance(usage, dict):
            calls += int(usage.get("calls") or 0)
            input_tokens += int(usage.get("input_tokens") or 0)
            output_tokens += int(usage.get("output_tokens") or 0)
            if usage.get("cost_usd") is not None:
                cost_usd += float(usage["cost_usd"])
            if usage.get("cost") is not None and usage.get("currency"):
                cost_by_currency[str(usage["currency"])] += float(usage["cost"])
            elif usage.get("cost_usd") is not None:
                cost_by_currency["USD"] += float(usage["cost_usd"])

    total_claims = sum(by_verdict.values())
    insufficient_claims = by_verdict.get("insufficient", 0)
    return {
        "total_checks": len(rows),
        "by_status": dict(by_status),
        "total_claims": total_claims,
        "by_verdict": dict(by_verdict),
        "insufficient_claims": insufficient_claims,
        "insufficient_rate": round(insufficient_claims / total_claims, 4) if total_claims else 0.0,
        "injection_detected": injection_detected,
        "search_errors": search_errors,
        "live_lookups": live_lookups,
        "duration_ms": {
            "median": int(statistics.median(durations)) if durations else 0,
            "max": max(durations) if durations else 0,
        },
        "usage": {
            "calls": calls,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": round(cost_usd, 6),
            "cost_by_currency": {k: round(v, 6) for k, v in sorted(cost_by_currency.items())},
        },
    }


def delete_checks(conn: Connection, check_ids: Iterable[str]) -> int:
    """Delete check log rows by id; returns how many existed."""
    ids = list(check_ids)
    if not ids:
        return 0
    return conn.execute(query("checks.delete"), {"ids": ids}).rowcount


def share_check(conn: Connection, check_id: str) -> dict[str, Any] | None:
    """Give a check a random public share key (kept if it already has one). None: no such check."""
    return conn.execute(
        query("checks.share"),
        {"check_id": check_id, "share_id": secrets.token_urlsafe(12), "now": utcnow_iso()},
    ).fetchone()


def unshare_check(conn: Connection, check_id: str) -> bool:
    return conn.execute(query("checks.unshare"), {"check_id": check_id}).fetchone() is not None


def shared_check(conn: Connection, share_id: str) -> dict[str, Any] | None:
    """The stored (masked) result behind a share key, exactly as logged at check time."""
    row = conn.execute(query("checks.by_share"), {"share_id": share_id}).fetchone()
    if row is None:
        return None
    return {**row, "result": json.loads(row.pop("result_json"))}


def prune_checks(conn: Connection, older_than: datetime) -> int:
    """Retention: delete check log rows created before `older_than` (compared in Python, UTC)."""
    old = [
        r["check_id"] for r in conn.execute(query("checks.created"))
        if (t := parse_ts(r["created_at"], naive_tz=timezone.utc)) is None or t < older_than
    ]
    return delete_checks(conn, old)


def purge(conn: Connection, *, urls: Iterable[str] = (), sources: Iterable[str] = ()) -> dict[str, int]:
    """Procedure of removal (docs/DANE_I_LICENCJE.md): delete every version of the records that
    cite one of `urls` or belong to one of `sources`, their passages and vectors, the source rows
    of `sources`, and the check logs that quote or link a deleted URL. One transaction."""
    params = {"urls": list(urls), "sources": list(sources)}
    out = {"evidence": 0, "passages": 0, "sources": 0, "checks": 0}
    if not params["urls"] and not params["sources"]:
        return out
    with conn.transaction():
        rows = conn.execute(query("evidence.purge_targets"), params).fetchall()
        ids = [r["evidence_id"] for r in rows]
        if ids:
            out["passages"] = conn.execute(query("passages.delete_by_evidence"), {"ids": ids}).rowcount
            conn.execute(query("reprints.delete_by_evidence"), {"ids": ids})
            out["evidence"] = conn.execute(query("evidence.delete"), {"ids": ids}).rowcount
        if params["sources"]:
            out["sources"] = conn.execute(query("sources.delete"), params).rowcount
        for url in sorted({r["citation_url"] for r in rows} | set(params["urls"])):
            out["checks"] += conn.execute(query("checks.delete_citing"), {"url": url}).rowcount
        if ids or out["sources"]:
            _bump_corpus_version(conn)
    if ids:
        from .reprints import refresh

        refresh(conn, recheck=True)  # reprints of a deleted original get a new one (or none)
    return out
