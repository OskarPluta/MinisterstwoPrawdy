from datetime import datetime

import pytest
from conftest import FIXTURES

from rag.evaluation import evaluate_retrieval, key, load_cases
from rag.search import Filters, fts_query, rrf


def test_rrf_formula_and_dedup():
    fused = rrf({"a": [1, 2, 3], "b": [3, 1]}, k=60)
    scores = {pid: s for pid, s, _ in fused}
    assert scores[1] == pytest.approx(1 / 61 + 1 / 62)
    assert scores[3] == pytest.approx(1 / 63 + 1 / 61)
    assert scores[2] == pytest.approx(1 / 62)
    assert [pid for pid, _, _ in fused] == [1, 3, 2]
    ranks = {pid: r for pid, _, r in fused}
    assert ranks[3] == {"a": 3, "b": 1}


def test_fts_query_folds_polish_letters_and_prefixes():
    q = fts_query("Czy wiatr na Mazowszu łamie drzewa?")
    assert q is not None
    terms = q.split(" | ")
    assert "czy" not in terms and "na" not in terms
    assert "mazow:*" in terms
    assert "lamie" in terms  # folded the same way as the indexed text ("ł" -> "l")


def test_fts_query_is_not_an_injection_vector(retriever):
    # tsquery operators and SQL quoting in user input are dropped by tokenization.
    hostile = "x' ); DROP TABLE evidence; -- & | ! :* <-> (a"
    q = fts_query(hostile)
    assert q is not None and not set(q) & set("'();&!<>-") - {"|"}
    retriever.bm25(hostile)  # must not raise
    assert retriever.conn.execute("SELECT COUNT(*) AS n FROM evidence").fetchone()["n"] > 0


def test_bm25_exact_keyword(retriever):
    hits = retriever.hybrid("CERT-2026-0412 kurier SMS", mode="bm25", shortlist=3)
    assert key(hits[0].meta) == "cert:CERT-2026-0412"


def test_superseded_version_not_returned(retriever):
    hits = retriever.hybrid("silny wiatr powiat legionowski", shortlist=8)
    wind = [h for h in hits if h.meta["upstream_id"] == "OST-2026-1121-MAZ-WIATR"]
    assert wind and all(h.meta["version"] == 2 for h in wind)


def test_filters_area_and_time(retriever):
    f = Filters(area="podlaskie")
    hits = retriever.hybrid("ostrzeżenie", filters=f, shortlist=8)
    assert hits
    assert all(not h.meta["areas"] or any("podlaskie" in a for a in h.meta["areas"]) for h in hits)

    f = Filters(since=datetime.fromisoformat("2026-11-21T00:00:00+01:00"), kinds={"warning_meteo"})
    keys = {key(h.meta) for h in retriever.hybrid("ostrzeżenie oblodzenie wiatr", filters=f)}
    assert "imgw_meteo:OST-2026-1118-POD-OBLODZENIE" not in keys
    assert "imgw_meteo:OST-2026-1121-MAZ-WIATR" in keys


def test_syndicated_copies_share_group_limit(retriever):
    hits = retriever.hybrid("nowy rozkład jazdy pociągów 13 grudnia", shortlist=8, per_group=1)
    groups = [
        h.meta["syndicated_from"] or f"{h.meta['source_id']}:{h.meta['upstream_id']}" for h in hits
    ]
    assert groups.count("mi_gov:MI-2026-11-12-ROZKLAD-JAZDY") == 1


def test_dense_index_refreshes_after_ingest(retriever, corpus, embedder):
    from rag.ingest import ingest_corpus

    retriever.dense("test")  # load matrix
    rec = {
        "source_id": "cert", "upstream_id": "CERT-2026-9999", "kind": "article",
        "title": "Testowy komunikat o zebrach", "citation_url": "https://moje.cert.test/x",
        "sections": [{"heading": None, "text": "Zebry w paski kradną hasła do poczty."}],
    }
    ingest_corpus(retriever.conn, {"sources": [], "evidence": [rec]}, embedder)
    hits = retriever.hybrid("zebry w paski", mode="dense", shortlist=1)
    assert hits[0].meta["upstream_id"] == "CERT-2026-9999"


@pytest.mark.parametrize("mode,floor_r3,floor_mrr", [("bm25", 0.75, 0.75), ("hybrid", 0.85, 0.80)])
def test_retrieval_quality_floor(retriever, mode, floor_r3, floor_mrr):
    """Regression floor on the fake set with the hashing embedder (real e5 numbers come
    from scripts/eval_retrieval.py)."""
    cases = load_cases(FIXTURES / "retrieval_cases.yaml")["cases"]
    s = evaluate_retrieval(retriever, cases, mode)["summary"]["ALL"]
    assert s["recall@3"] >= floor_r3
    assert s["mrr"] >= floor_mrr


def test_one_passage_per_article_by_default(retriever):
    query = "nowy rozkład jazdy pociągów połączenia regionalne bilety"
    hits = retriever.hybrid(query, shortlist=8)
    mi = [h for h in hits if h.meta["upstream_id"] == "MI-2026-11-12-ROZKLAD-JAZDY"]
    assert len(mi) == 1
    wider = retriever.hybrid(query, shortlist=8, per_article=3)
    assert len([h for h in wider if h.meta["upstream_id"] == "MI-2026-11-12-ROZKLAD-JAZDY"]) >= 2
