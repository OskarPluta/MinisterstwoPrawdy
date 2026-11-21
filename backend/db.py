"""PostgreSQL storage: sources, versioned evidence, passages (+full-text, vectors), checks.

All SQL lives in backend/database/ (schema.sql, queries/*.sql) and is executed with
bound parameters only.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Literal

import numpy as np
import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from .config import database_url
from .database import query, schema
from .embed import _fold
from .timeutil import utcnow_iso

SCHEMA_VERSION = 1

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
            if _one(conn.execute(query("meta.has_meta")))["present"]:
                row = conn.execute(query("meta.get_schema_version")).fetchone()
                found = int(row["value"]) if row else 0
                if found > SCHEMA_VERSION:
                    raise RuntimeError(
                        f"database schema v{found} is newer than this code (v{SCHEMA_VERSION})"
                    )
            conn.execute(schema())  # static DDL from schema.sql
            # Schema changes after v1 go here as steps for found < version <= SCHEMA_VERSION.
            conn.execute(query("meta.set_schema_version"), {"version": str(SCHEMA_VERSION)})
    except BaseException:
        conn.close()
        raise
    return conn


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
    "publisher", "publishing_domain", "source_type", "terms_url", "citation_url", "refresh_policy",
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
    action: Literal["inserted", "new_version", "unchanged", "known_version"]
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
    action = "unchanged" if row["is_current"] else "known_version"
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
    """Counts before chunking (documents/records) plus passages, per docs/plan.md §1."""
    return dict(conn.execute(query("meta.corpus_totals")).fetchone() or {})


def record_check(conn: Connection, row: dict[str, Any]) -> None:
    conn.execute(query("checks.insert"), row)
