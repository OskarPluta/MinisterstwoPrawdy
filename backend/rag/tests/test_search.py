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


# --- claim area and period as soft ranking lists -------------------------------------------

from rag.areas import area_matches, area_score, normalize_area  # noqa: E402
from rag.search import Prefer, in_period, supersessions  # noqa: E402

W = "+01:00"  # Europe/Warsaw in November


def _keys(hits):
    return [key(h.meta) for h in hits]


@pytest.mark.parametrize("claim_area,areas,expected", [
    ("Mazowsze", ["województwo mazowieckie"], True),
    ("całe Mazowsze", ["powiat legionowski (mazowieckie)"], True),
    ("na Mazowszu", ["powiat otwocki (mazowieckie)"], True),
    ("województwo mazowieckie", ["Mazowsze"], True),
    ("Podlasie", ["powiat białostocki (podlaskie)"], True),
    ("Lubelszczyzna", ["województwo lubelskie"], True),
    ("Pomorze Zachodnie", ["województwo zachodniopomorskie"], True),
    ("Kujawy", ["rzeka Brda (kujawsko-pomorskie)"], True),
    ("w Kielcach", ["Kielce", "województwo świętokrzyskie"], True),
    ("Łeba", ["Łebie"], True),
    ("cała Polska", ["województwo lubelskie"], True),
    ("w całym kraju", ["Polska"], True),
    ("Mazowsze", ["województwo lubelskie"], False),
    ("pomorskie", ["województwo zachodniopomorskie"], False),  # another voivodeship, not a substring match
    ("Śląsk", ["województwo dolnośląskie"], False),
    ("Kraków", ["Kraśnik"], False),
    ("Włochy", ["Polska"], False),
    ("Mazowsze", [], None),  # no areas on the record: unknown, neither match nor mismatch
])
def test_area_normalization(claim_area, areas, expected):
    assert area_matches(claim_area, areas) is expected


def test_area_score_prefers_the_same_area_over_national():
    assert normalize_area("całe Mazowsze").regions == normalize_area("województwo mazowieckie").regions
    assert area_score("Mazowsze", ["powiat legionowski (mazowieckie)"]) > area_score("Mazowsze", ["Polska"]) > 0


def test_area_filter_understands_voivodeship_forms(retriever):
    """The hard filter used to be a folded substring test: "Mazowsze" missed "(mazowieckie)"."""
    keys = _keys(retriever.hybrid("ostrzeżenie silny wiatr", filters=Filters(area="Mazowsze")))
    assert "imgw_meteo:OST-2026-1121-MAZ-WIATR" in keys
    assert "imgw_meteo:OST-2026-1118-POD-OBLODZENIE" not in keys  # podlaskie


def test_soft_area_list_boosts_without_dropping(retriever):
    q = "ostrzeżenie meteorologiczne"
    plain = retriever.hybrid(q, shortlist=8)
    boosted = retriever.hybrid(q, shortlist=8, prefer=Prefer(area="Podlasie"))
    assert _keys(plain)[0] != "imgw_meteo:OST-2026-1118-POD-OBLODZENIE"
    assert _keys(boosted)[0] == "imgw_meteo:OST-2026-1118-POD-OBLODZENIE"
    assert "area" in boosted[0].ranks
    # Soft: records outside the area stay (nothing filtered), and nothing new is pulled in.
    assert "imgw_meteo:OST-2026-1121-MAZ-WIATR" in _keys(boosted)
    assert set(_keys(boosted)) <= set(_keys(retriever.hybrid(q, shortlist=50)))


def test_soft_period_list_boosts_the_record_of_the_period(retriever):
    q = "ostrzeżenie meteorologiczne"
    night = Prefer(since=datetime.fromisoformat("2026-11-21T22:00" + W),
                   until=datetime.fromisoformat("2026-11-22T06:00" + W))
    assert _keys(retriever.hybrid(q, shortlist=8))[0] != "imgw_meteo:OST-2026-1121-LDZ-MGLA"
    hits = retriever.hybrid(q, shortlist=8, prefer=night)
    assert _keys(hits)[0] == "imgw_meteo:OST-2026-1121-LDZ-MGLA"
    assert "imgw_meteo:OST-2026-1118-POD-OBLODZENIE" in _keys(hits)  # expired, but not dropped


def test_until_applies_only_to_timed_and_structured_records(retriever):
    hits = {key(h.meta): h.meta for h in retriever.hybrid("ostrzeżenie wiatr rozkład jazdy", shortlist=50)}
    p = Prefer(since=datetime.fromisoformat("2026-11-01T00:00" + W),
               until=datetime.fromisoformat("2026-11-10T00:00" + W))
    # An article published after the period may report on it: `until` is not applied.
    assert in_period(hits["mi_gov:MI-2026-11-12-ROZKLAD-JAZDY"], p)
    # A warning that starts after the period does not describe it.
    assert not in_period(hits["imgw_meteo:OST-2026-1121-MAZ-WIATR"], p)
    # `since` applies to everything: an article from before the period is not boosted ...
    early = Prefer(since=datetime.fromisoformat("2026-11-15T00:00" + W))
    assert not in_period(hits["mi_gov:MI-2026-11-12-ROZKLAD-JAZDY"], early)
    # ... unless the period is still ahead: an article published by now may announce it.
    future = Prefer(since=datetime.fromisoformat("2026-12-13T00:00" + W), now=datetime.fromisoformat("2026-11-21T12:00" + W))
    assert in_period(hits["mi_gov:MI-2026-11-12-ROZKLAD-JAZDY"], future)
    assert not in_period(hits["imgw_meteo:OST-2026-1121-MAZ-WIATR"], future)


# --- newer record of the same source wins ---------------------------------------------------

EARLY_WIND = {
    "source_id": "imgw_meteo", "upstream_id": "OST-2026-1120-MAZ-WIATR-WSTEPNE", "kind": "warning_meteo",
    "title": "Ostrzeżenie meteorologiczne: silny wiatr (mazowieckie)",
    "citation_url": "https://danepubliczne.imgw.test/ostrzezenia/OST-2026-1120-MAZ-WIATR-WSTEPNE",
    "published_at": "2026-11-20T18:00:00Z", "valid_from": "2026-11-21T10:00:00Z", "valid_to": "2026-11-21T19:00:00Z",
    "areas": ["powiat legionowski (mazowieckie)", "powiat otwocki (mazowieckie)"], "status": "aktywne",
    "fields": {"issuer": "IMGW-PIB", "phenomenon": "silny wiatr", "level": 1, "probability": 60,
               "description": "Wstępne ostrzeżenie: porywy wiatru do 60 km/h."},
}


def add_records(retriever, embedder, *records):
    from rag.ingest import ingest_corpus

    ingest_corpus(retriever.conn, {"sources": [], "evidence": list(records)}, embedder)


def test_newer_record_of_the_same_matter_ranks_first(retriever, embedder):
    add_records(retriever, embedder, EARLY_WIND)
    hits = retriever.hybrid(["wstępne ostrzeżenie porywy wiatru 60 km/h", "silny wiatr Mazowsze"], shortlist=8)
    keys = _keys(hits)
    old, new = "imgw_meteo:OST-2026-1120-MAZ-WIATR-WSTEPNE", "imgw_meteo:OST-2026-1121-MAZ-WIATR"
    assert old in keys and new in keys  # both stay: the older one is shown as superseded
    assert keys.index(new) < keys.index(old)
    sup = supersessions(h.meta for h in hits)
    by_key = {key(h.meta): h.evidence_id for h in hits}
    assert sup[by_key[old]] == by_key[new]


def test_reprint_does_not_supersede_its_original(retriever):
    hits = retriever.hybrid("nowy rozkład jazdy pociągów 13 grudnia", shortlist=8)
    keys = _keys(hits)
    assert {"mi_gov:MI-2026-11-12-ROZKLAD-JAZDY", "portal:PORTAL-2026-11-12-ROZKLAD"} <= set(keys)
    assert supersessions(h.meta for h in hits) == {}  # one text, not an older and a newer record


def test_different_phenomena_or_areas_are_not_the_same_matter(retriever):
    hits = retriever.hybrid("ostrzeżenie meteorologiczne", shortlist=8)
    assert supersessions(h.meta for h in hits) == {}  # wind/mazowieckie, ice/podlaskie, fog/łódzkie
