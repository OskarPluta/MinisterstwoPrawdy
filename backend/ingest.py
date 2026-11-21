"""Load a corpus file (fixture or importer output) into the database."""

from __future__ import annotations

import json
import logging
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .chunking import chunk_record, embedding_text
from .database import query
from .db import (
    Connection,
    PassageIn,
    content_hash,
    find_by_hash,
    set_embeddings,
    source_domains,
    upsert_evidence,
    upsert_source,
)
from .embed import Embedder

log = logging.getLogger(__name__)

BATCH_RECORDS = 256  # records chunked and embedded together (bounded memory, large E5 batches)
_REQUIRED = ("source_id", "upstream_id", "kind", "title", "citation_url")


def load_corpus_file(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def check_record(record: dict[str, Any], domains: dict[str, str]) -> None:
    """Raise ValueError unless the record names a known source and cites an https URL
    on that source's publishing domain (or a subdomain of it)."""
    missing = [k for k in _REQUIRED if not record.get(k)]
    if missing:
        raise ValueError(f"missing {missing}")
    domain = domains.get(record["source_id"])
    if domain is None:
        raise ValueError(f"unknown source {record['source_id']!r}")
    url = urlparse(record["citation_url"])
    host = (url.hostname or "").lower()
    domain = domain.lower()
    if url.scheme != "https" or not (host == domain or host.endswith("." + domain)):
        raise ValueError(f"citation_url {record['citation_url']!r} is not https on {domain}")


def ingest_corpus(
    conn: Connection, corpus: dict[str, Any], embedder: Embedder | None
) -> Counter[str]:
    """Upsert sources and evidence. Records are processed in file order, so a later
    entry with the same (source_id, upstream_id) acts as an amendment. Invalid records
    are logged and counted as "invalid"; the rest of the file still loads."""
    for src in corpus.get("sources", []):
        upsert_source(conn, src)
    domains = source_domains(conn)

    stats: Counter[str] = Counter()
    records = corpus.get("evidence", [])
    for start in range(0, len(records), BATCH_RECORDS):
        pending: list[tuple[dict[str, Any], list[PassageIn]]] = []
        for record in records[start : start + BATCH_RECORDS]:
            try:
                check_record(record, domains)
                # Skip unchanged content before chunking and embedding it.
                found = find_by_hash(conn, record, content_hash(record))
                if found is not None:
                    stats[found.action] += 1
                    continue
                pending.append((record, chunk_record(record)))
            except (KeyError, TypeError, ValueError) as e:
                stats["invalid"] += 1
                log.warning("skipped %s:%s: %s", record.get("source_id"), record.get("upstream_id"), e)

        if embedder is not None:
            passages = [(r["title"], p) for r, ps in pending for p in ps]
            if passages:
                vecs = embedder.embed_passages([embedding_text(t, p.heading, p.text) for t, p in passages])
                for (_, p), v in zip(passages, vecs):
                    p.embedding = v
        model = embedder.name if embedder else None
        for record, ps in pending:
            stats[upsert_evidence(conn, record, ps, model).action] += 1
    return stats


def reembed(conn: Connection, embedder: Embedder) -> int:
    """Recompute vectors for all passages of current evidence with a new model."""
    rows = conn.execute(query("passages.current_for_embedding")).fetchall()
    vecs = embedder.embed_passages([embedding_text(r["title"], r["heading"], r["search_text"]) for r in rows])
    set_embeddings(conn, zip([r["passage_id"] for r in rows], vecs), embedder.name)
    return len(rows)
