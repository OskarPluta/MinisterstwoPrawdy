import copy

import pytest

from rag.db import connect, corpus_totals, upsert_source
from rag.embed import HashEmbedder
from rag.ingest import ingest_corpus


def _distinct_keys(corpus):
    return {(e["source_id"], e["upstream_id"]) for e in corpus["evidence"]}


def test_totals_count_records_before_chunking(conn, corpus):
    t = corpus_totals(conn)
    assert t["distinct_records"] == len(_distinct_keys(corpus))
    assert t["current_records"] == len(_distinct_keys(corpus))
    assert t["passages_searchable"] >= t["current_records"]
    assert t["source_types"] >= 2


def test_second_import_creates_no_duplicates(conn, corpus, embedder):
    before = corpus_totals(conn)
    n_passages = conn.execute("SELECT COUNT(*) AS n FROM passages").fetchone()["n"]
    stats = ingest_corpus(conn, corpus, embedder)
    assert stats["inserted"] == 0 and stats["new_version"] == 0
    assert corpus_totals(conn) == before
    assert conn.execute("SELECT COUNT(*) AS n FROM passages").fetchone()["n"] == n_passages


def test_amended_warning_creates_new_version_and_hides_old(conn):
    rows = conn.execute(
        "SELECT version, is_current, superseded_by, fields_json FROM evidence "
        "WHERE upstream_id = 'OST-2026-1121-MAZ-WIATR' ORDER BY version"
    ).fetchall()
    assert [r["version"] for r in rows] == [1, 2]
    assert [r["is_current"] for r in rows] == [False, True]
    assert rows[0]["superseded_by"] is not None
    assert '"level": 2' in rows[1]["fields_json"]

    # The old version's passages are kept (traceable citations) but not searchable.
    old = conn.execute(
        "SELECT p.passage_id, p.fts FROM passages p JOIN evidence e USING (evidence_id) "
        "WHERE e.upstream_id = 'OST-2026-1121-MAZ-WIATR' AND e.version = 1"
    ).fetchall()
    assert old
    assert all(r["fts"] is None for r in old)


def test_new_amendment_after_import(conn, corpus, embedder):
    rec = copy.deepcopy(next(e for e in corpus["evidence"] if e["upstream_id"] == "CERT-2026-0412"))
    rec["sections"][0]["text"] += "\n\nAktualizacja: kampania objęła także wiadomości e-mail."
    stats = ingest_corpus(conn, {"sources": [], "evidence": [rec]}, embedder)
    assert stats["new_version"] == 1
    assert corpus_totals(conn)["distinct_records"] == len(_distinct_keys(corpus))


def test_unchanged_content_ignores_fetch_time(new_db, corpus, embedder):
    c = new_db()
    ingest_corpus(c, corpus, embedder)
    refetched = copy.deepcopy(corpus)
    for e in refetched["evidence"]:
        e["fetched_at"] = "2026-11-22T00:00:00Z"
    stats = ingest_corpus(c, refetched, embedder)
    assert stats["inserted"] == 0 and stats["new_version"] == 0


def test_invalid_records_are_skipped_and_the_rest_loads(new_db, embedder):
    c = new_db()
    src = {"source_id": "cert", "publisher": "CERT", "publishing_domain": "cert.test", "source_type": "html"}
    good = {"source_id": "cert", "upstream_id": "OK", "kind": "article", "title": "T",
            "citation_url": "https://moje.cert.test/ok", "sections": [{"text": "Treść komunikatu."}]}
    bad = [
        {**good, "upstream_id": "HTTP", "citation_url": "http://moje.cert.test/x"},
        {**good, "upstream_id": "HOST", "citation_url": "https://cert.test.evil.example/x"},
        {**good, "upstream_id": "JS", "citation_url": "javascript:alert(1)"},
        {**good, "upstream_id": "SRC", "source_id": "nope"},
        {**good, "upstream_id": "EMPTY", "sections": []},
        {**good, "upstream_id": "FIELDS", "kind": "warning_meteo", "fields": {"phenomenon": "X"}},
    ]
    stats = ingest_corpus(c, {"sources": [src], "evidence": [*bad, good]}, embedder)
    assert stats["invalid"] == len(bad) and stats["inserted"] == 1


def test_reloading_sources_keeps_fetch_status(new_db):
    c = new_db()
    base = {"source_id": "s", "publisher": "P", "publishing_domain": "p.test", "source_type": "html"}
    upsert_source(c, {**base, "last_error": "timeout", "last_success_at": "2026-11-20T10:00:00Z"})
    upsert_source(c, {**base, "publisher": "P2"})  # catalog reload without status
    row = c.execute("SELECT publisher, last_error, last_success_at FROM sources").fetchone()
    assert tuple(row.values()) == ("P2", "timeout", "2026-11-20T10:00:00Z")
    upsert_source(c, {**base, "last_error": None})  # an explicit status clears it
    assert c.execute("SELECT last_error FROM sources").fetchone()["last_error"] is None


def test_unchanged_records_are_not_reembedded(new_db, corpus):
    class CountingEmbedder(HashEmbedder):
        texts = 0

        def embed_passages(self, texts):
            CountingEmbedder.texts += len(texts)
            return super().embed_passages(texts)

    emb = CountingEmbedder()
    c = new_db()
    ingest_corpus(c, corpus, emb)
    first = CountingEmbedder.texts
    assert first > 0
    ingest_corpus(c, corpus, emb)
    assert CountingEmbedder.texts == first


def test_newer_schema_is_refused(new_db, database_url):
    c = new_db()
    c.execute("UPDATE meta SET value = '99' WHERE key = 'schema_version'")
    schema = c.execute("SELECT current_schema() AS s").fetchone()["s"]
    with pytest.raises(RuntimeError, match="newer"):
        connect(database_url, db_schema=schema)


def test_reconnect_keeps_data(new_db, database_url, embedder):
    c = new_db()
    src = {"source_id": "s", "publisher": "P", "publishing_domain": "p.test", "source_type": "html"}
    upsert_source(c, src)
    schema = c.execute("SELECT current_schema() AS s").fetchone()["s"]
    again = connect(database_url, db_schema=schema)
    try:
        assert corpus_totals(again)["publishing_domains"] == 1
    finally:
        again.close()


def test_sql_files_load_and_are_parameterized():
    from rag.database import query, schema

    assert "CREATE TABLE IF NOT EXISTS evidence" in schema()
    assert "%(content_hash)s" in query("evidence.find_by_hash")
    with pytest.raises(KeyError):
        query("evidence.nope")
