import copy
import json

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
    from database import query, schema

    assert "CREATE TABLE IF NOT EXISTS evidence" in schema()
    assert "%(content_hash)s" in query("evidence.find_by_hash")
    with pytest.raises(KeyError):
        query("evidence.nope")


# ---------------------------------------------------------------------------------------
# Licences, removal procedure and check log retention (docs/DANE_I_LICENCJE.md)
# ---------------------------------------------------------------------------------------

def _count(c, sql, **params):
    return c.execute(sql, params).fetchone()["n"]


def _add_check(c, check_id, created_at, cites=""):
    from rag.db import record_check

    record_check(c, {
        "check_id": check_id, "created_at": created_at, "input_masked": "x", "status": "ok",
        "result_json": json.dumps({"claims": [{"citations": [{"url": cites}]}]}), "corpus_version": 0,
        "model": None, "embedding_model": None, "duration_ms": 1,
    })


def test_purge_url_deletes_every_version_passages_and_citing_checks(conn, corpus, embedder):
    from rag.db import corpus_version, purge
    from crawler.removals import Removals

    url = "https://danepubliczne.imgw.test/ostrzezenia/OST-2026-1121-MAZ-WIATR"
    _add_check(conn, "c-cites", "2026-11-21T10:00:00Z", cites=url)
    _add_check(conn, "c-other", "2026-11-21T10:00:00Z", cites="https://inne.test/")
    v0 = corpus_version(conn)
    n_before = _count(conn, "SELECT COUNT(*) AS n FROM evidence")

    out = purge(conn, urls=[url])
    assert out["evidence"] == 2 and out["passages"] > 0 and out["checks"] == 1 and out["sources"] == 0
    assert _count(conn, "SELECT COUNT(*) AS n FROM evidence WHERE upstream_id = 'OST-2026-1121-MAZ-WIATR'") == 0
    assert _count(conn, "SELECT COUNT(*) AS n FROM evidence") == n_before - 2
    assert _count(conn, "SELECT COUNT(*) AS n FROM passages p LEFT JOIN evidence e USING (evidence_id) "
                        "WHERE e.evidence_id IS NULL") == 0, "no orphaned passages"
    assert [r["check_id"] for r in conn.execute("SELECT check_id FROM checks")] == ["c-other"]
    assert corpus_version(conn) > v0

    # The register of removed content keeps it out of the next ingest.
    stats = ingest_corpus(conn, corpus, embedder, removals=Removals(urls=frozenset({url})))
    assert stats["removed"] == 2 and stats["inserted"] == 0
    assert _count(conn, "SELECT COUNT(*) AS n FROM evidence WHERE upstream_id = 'OST-2026-1121-MAZ-WIATR'") == 0


def test_purge_source_deletes_source_and_records(conn):
    from rag.db import purge

    out = purge(conn, sources=["cert"])
    assert out["sources"] == 1 and out["evidence"] >= 1
    assert _count(conn, "SELECT COUNT(*) AS n FROM sources WHERE source_id = 'cert'") == 0
    assert _count(conn, "SELECT COUNT(*) AS n FROM evidence WHERE source_id = 'cert'") == 0
    assert purge(conn) == {"evidence": 0, "passages": 0, "sources": 0, "checks": 0}, "nothing given, nothing deleted"


def test_ingest_skips_removed_source(new_db, corpus, embedder):
    from crawler.removals import Removals

    c = new_db()
    stats = ingest_corpus(c, corpus, embedder, removals=Removals(sources=frozenset({"cert"})))
    assert stats["removed"] >= 1
    assert _count(c, "SELECT COUNT(*) AS n FROM sources WHERE source_id = 'cert'") == 0


def test_prune_and_delete_checks(new_db):
    from datetime import datetime, timezone

    from rag.db import delete_checks, prune_checks

    c = new_db()
    _add_check(c, "old", "2026-10-01T00:00:00Z")
    _add_check(c, "new", "2026-11-21T00:00:00Z")
    _add_check(c, "naive", "2026-10-01T00:00:00")  # no offset: read as UTC
    assert prune_checks(c, datetime(2026, 11, 1, tzinfo=timezone.utc)) == 2
    assert [r["check_id"] for r in c.execute("SELECT check_id FROM checks")] == ["new"]
    assert delete_checks(c, ["new"]) == 1 and delete_checks(c, ["new"]) == 0


def test_source_licence_fields_are_stored(conn):
    row = conn.execute("SELECT license, attribution, processed_notice FROM sources WHERE source_id = 'imgw_hydro'").fetchone()
    assert row["license"] and row["attribution"].startswith("Źródłem") and row["processed_notice"]


def test_v1_database_is_migrated(new_db, database_url):
    from rag.db import SCHEMA_VERSION, share_check

    c = new_db()
    c.execute("ALTER TABLE sources DROP COLUMN license, DROP COLUMN attribution, DROP COLUMN processed_notice")
    c.execute("ALTER TABLE checks DROP COLUMN share_id, DROP COLUMN shared_at")
    c.execute("UPDATE meta SET value = '1' WHERE key = 'schema_version'")
    schema = c.execute("SELECT current_schema() AS s").fetchone()["s"]
    again = connect(database_url, db_schema=schema)
    try:
        upsert_source(again, {"source_id": "s", "publisher": "P", "publishing_domain": "p.test",
                              "source_type": "official", "license": "CC BY 4.0"})
        assert again.execute("SELECT license FROM sources").fetchone()["license"] == "CC BY 4.0"
        assert again.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()["value"] == str(SCHEMA_VERSION)
        _add_check(again, "c1", "2026-11-21T10:00:00Z")
        assert share_check(again, "c1")["share_id"], "v3 share columns added"
    finally:
        again.close()


def test_verifier_retention_from_environment(conn, monkeypatch):
    from datetime import datetime, timezone

    from rag.search import Retriever
    from rag.verify import Verifier

    monkeypatch.setenv("PRAWDOWID_CHECKS_RETENTION_DAYS", "0")
    assert Verifier(Retriever(conn, None), None, live=False).record is False, "0 days: no check log"

    monkeypatch.setenv("PRAWDOWID_CHECKS_RETENTION_DAYS", "7")
    v = Verifier(Retriever(conn, None), None, live=False)
    _add_check(conn, "old", "2026-11-01T00:00:00Z")
    _add_check(conn, "fresh", "2026-11-20T00:00:00Z")
    now = datetime(2026, 11, 22, tzinfo=timezone.utc)
    assert v.prune(now) == 1 and v.prune(now) == 0, "at most once per hour"
    assert [r["check_id"] for r in conn.execute("SELECT check_id FROM checks")] == ["fresh"]
