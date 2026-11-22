"""Live lookup (rag/live.py) on recorded REAL responses (tests/fixtures/live/*.json, recorded
2026-11-22 from factchecktools.googleapis.com, mcp.euvsd.info and www.gov.pl). HTTP is replayed
through httpx.MockTransport under the real crawler Fetcher, so robots.txt handling, the private
API-key headers and the response checks are exercised. No network."""

from __future__ import annotations

import json
import threading
from collections import defaultdict, deque
from typing import Any

import httpx
import pytest
from conftest import FIXTURES
from test_verify import NOW, FakeLLM, claim, verdict

from crawler.config import SETTINGS_DEFAULTS
from crawler.fetcher import Fetcher
from rag import live
from rag.ingest import ingest_corpus
from rag.search import Retriever
from rag.verify import Verifier, render_markdown

LIVE = FIXTURES / "live"


def recorded(name: str) -> dict[str, Any]:
    return json.loads((LIVE / f"{name}.json").read_text(encoding="utf-8"))


class Replay:
    """Serves recorded exchanges by (method, URL), in recorded order; anything else is a 404.
    Remembers the requests (with headers) for assertions."""

    def __init__(self, *names: str, override: dict[tuple[str, str], int] | None = None):
        self.queue: dict[tuple[str, str], deque] = defaultdict(deque)
        for n in names:
            for e in recorded(n)["exchanges"]:
                self.queue[(e["method"], e["url"])].append(e)
        self.override = override or {}
        self.seen: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        key = (request.method, str(request.url))
        if key in self.override:
            return httpx.Response(self.override[key])
        q = self.queue.get(key)
        if not q:
            return httpx.Response(404)
        e = q.popleft() if len(q) > 1 else q[0]
        headers = {k: v for k, v in (("content-type", e["content_type"]), ("location", e["location"])) if v}
        return httpx.Response(e["status"], headers=headers, content=e["body"].encode("utf-8"))


@pytest.fixture
def no_wait(monkeypatch):
    """The Fetcher still records each request per host; only the 3 s sleep is skipped."""
    monkeypatch.setattr(Fetcher, "_wait_turn", lambda self, host, crawl_delay=None: None)


@pytest.fixture
def keys(monkeypatch):
    monkeypatch.setenv("GOOGLE_FACTCHECK_API_KEY", "test-fc-key")
    monkeypatch.setenv("EUVSDISINFO_API_KEY", "test-euvsd-key")


def factory(replay: Replay):
    def make(_key: str) -> Fetcher:
        f = Fetcher({**SETTINGS_DEFAULTS, "http_timeout_seconds": 5}, live._MemoryStore(),  # type: ignore[arg-type]
                    "FaktBot/1.0 (+kontakt@example.test)")
        f.client = httpx.Client(transport=httpx.MockTransport(replay), headers=dict(f.client.headers),
                                follow_redirects=False)
        return f
    return make


def run_source(key: str, replay: Replay) -> live.SourceRun:
    rec = recorded(key)
    import time
    return live._run_source(key, live.SOURCES[key], time.monotonic() + 30, rec["claim"], rec["queries"],
                            factory(replay))


# ---- parsers on the recorded payloads -----------------------------------------------------


def test_factcheck_records_keep_trusted_reviewers_only():
    body = json.loads(recorded("factcheck")["exchanges"][-1]["body"])
    sources, records = live.factcheck_records(body, "2026-11-22T00:00:00Z")
    assert [s["source_id"] for s in sources] == ["live-factcheck-demagog-org-pl"]
    r = records[0]
    assert r["citation_url"].startswith("https://demagog.org.pl/") and r["kind"] == "factcheck"
    text = r["sections"][0]["text"]
    assert "Twierdzenie: Migranci w Warszawie sprowadzeni w ramach relokacji." in text
    assert "Ocena Demagog: Fałsz" in text
    # The same review on a domain outside the allowlist is dropped.
    body["claims"][0]["claimReview"][0]["url"] = "https://example.com/review"
    assert live.factcheck_records(body, "2026-11-22T00:00:00Z") == ([], [])


def test_euvsdisinfo_search_markdown_is_parsed():
    from crawler.fetcher import Resp
    from crawler.mcp import tool_text

    ex = recorded("euvsdisinfo")["exchanges"][-1]
    items = live.parse_euvsd_search(tool_text(Resp(url="", final_url="", status=200, text=ex["body"])))
    assert len(items) == 10
    assert {i["type"] for i in items} == {"Article", "Claim Review"}
    assert all(i["url"].startswith("https://euvsdisinfo.eu/") and i["content"] for i in items)
    flood = next(i for i in items if i["title"] == "Ukrainian arms flood into Poland")
    assert flood["content"].startswith("SUMMARY") and "underground Ukrainian army" in flood["content"]
    recs = live.euvsd_records([flood], "2026-11-22T00:00:00Z")
    assert recs[0]["kind"] == "factcheck"
    assert recs[0]["sections"][0]["heading"].startswith("Przypadek dezinformacji w bazie EUvsDisinfo")
    assert not recs[0]["sections"][0]["text"].startswith("SUMMARY")


def test_govpl_pick_prefers_matching_articles():
    rec = recorded("govpl")
    search = next(e for e in rec["exchanges"] if "/api/data/search" in e["url"])
    picks = live.govpl_pick(json.loads(search["body"])["results"], rec["claim"])
    assert len(picks) == 2 and all(p["url"].startswith("/web/") and "HPV" in p["title"] for p in picks)


def test_govpl_pick_orders_by_meaning_with_a_real_embedder():
    """Word overlap alone ranks "bezpłatne szczepienia … HPV" above "szczepienie przeciw grypie" for a
    flu claim; a semantic embedder puts the flu page first (stand-in vectors: flu vs anything else)."""
    class Topic:
        name = "e5-test"
        def embed_query(self, q):
            return [1.0, 0.0] if "grypie" in q else [0.0, 1.0]
        def embed_passages(self, ps):
            return [[1.0, 0.1] if "grypie" in p else [0.1, 1.0] for p in ps]
    results = [{"url": "/web/a/bezplatne-szczepienia-hpv", "title": "Bezpłatne szczepienia przeciw HPV dla seniorów 2026"},
               {"url": "/web/b/hpv-2", "title": "Bezpłatne szczepienia HPV w sezonie 2026"},
               {"url": "/web/gis/szczepienie-przeciw-grypie", "title": "Szczepienie przeciw grypie"}]
    claim = "Seniorzy mogą się bezpłatnie zaszczepić przeciw grypie w sezonie 2026"
    assert live.govpl_pick(results, claim, n=1)[0]["url"] != "/web/gis/szczepienie-przeciw-grypie"
    assert live.govpl_pick(results, claim, n=1, embedder=Topic(), claim=claim)[0]["url"] == "/web/gis/szczepienie-przeciw-grypie"


def test_short_query_keeps_three_content_words():
    assert live._short_query("migranci w Warszawie i relokacja paktu") == "migranci Warszawie relokacja"


# ---- sources through the real Fetcher -------------------------------------------------------


def test_govpl_source_respects_robots_and_extracts_articles(no_wait):
    replay = Replay("govpl")
    run = run_source("govpl", replay)
    assert run.error is None and run.requests == 3  # search + 2 articles (robots.txt is the Fetcher's)
    assert str(replay.seen[0].url) == "https://www.gov.pl/robots.txt"  # followed to /static/code/robots.txt
    assert all(r.headers["user-agent"].startswith("FaktBot/") for r in replay.seen)
    # The search asks with the claim's own wording; both picked articles are on www.gov.pl.
    assert "query=Od+1+pa" in str(next(r.url for r in replay.seen if "/api/data/search" in str(r.url)))
    assert {s["publisher"] for s in run.sources} == {"Powiatowa Stacja Sanitarno-Epidemiologiczna w Olsztynie",
                                                     "Powiatowa Stacja Sanitarno-Epidemiologiczna w Grodzisku Mazowieckim"}
    r = next(x for x in run.records if "/web/psse-olsztyn/" in x["citation_url"])
    assert r["citation_url"].startswith("https://www.gov.pl/web/psse-olsztyn/")
    assert "18" in r["sections"][0]["text"] and r["published_at"].endswith("Z")


def test_factcheck_key_goes_only_in_header(no_wait, keys):
    replay = Replay("factcheck")
    run = run_source("factcheck", replay)
    assert run.error is None and len(run.records) == 1
    api = [r for r in replay.seen if "claims:search" in str(r.url)]
    assert api and all(r.headers["x-goog-api-key"] == "test-fc-key" for r in api)
    assert all("test-fc-key" not in str(r.url) for r in replay.seen)


def test_euvsdisinfo_source_keeps_cases_first(no_wait, keys):
    replay = Replay("euvsdisinfo")
    run = run_source("euvsdisinfo", replay)
    assert run.error is None and run.requests == 2
    kinds = [r["kind"] for r in run.records]
    assert kinds == sorted(kinds, key=lambda k: k != "factcheck") and kinds.count("article") == 2
    assert "https://euvsdisinfo.eu/report/ukrainian-arms-flood-into-poland" in {r["citation_url"] for r in run.records}
    posts = [r for r in replay.seen if r.method == "POST"]
    assert all(r.headers["authorization"] == "Bearer test-euvsd-key" and r.headers["mcp-session-id"] for r in posts)


def test_missing_key_skips_source(no_wait, monkeypatch):
    monkeypatch.delenv("GOOGLE_FACTCHECK_API_KEY", raising=False)
    run = run_source("factcheck", Replay())
    assert run.skipped and not run.requests and not run.error


def test_stale_govpl_result_is_skipped(no_wait):
    rec = recorded("govpl")
    olsztyn = next(e["url"] for e in rec["exchanges"] if "/web/psse-olsztyn/" in e["url"])
    run = run_source("govpl", Replay("govpl", override={("GET", olsztyn): 404}))
    assert run.error is None and run.requests == 3
    assert [r["citation_url"] for r in run.records] == [e["url"] for e in rec["exchanges"] if "/web/psse-grodzisk" in e["url"]]


def test_throttled_source_fails_alone(no_wait):
    rec = recorded("govpl")
    search_url = next(e["url"] for e in rec["exchanges"] if "/api/data/search" in e["url"])
    run = run_source("govpl", Replay("govpl", override={("GET", search_url): 503}))
    assert run.error == "Throttled" and run.records == []


# ---- lookup into the database ------------------------------------------------------------------


def test_lookup_ingests_versioned_records_idempotently(conn, embedder, no_wait):
    rec = recorded("govpl")
    kw = {"sources": {"govpl": live.search_govpl}}
    first = live.live_lookup(conn, embedder, rec["claim"], rec["queries"], fetcher_factory=factory(Replay("govpl")), **kw)
    assert first.new_docs == 2 and len(first.evidence_ids) == 2
    row = conn.execute("SELECT source_id, citation_url FROM evidence WHERE evidence_id = %(i)s",
                       {"i": first.evidence_ids[0]}).fetchone()
    assert row["source_id"].startswith("live-govpl-") and row["citation_url"].startswith("https://www.gov.pl/web/")
    again = live.live_lookup(conn, embedder, rec["claim"], rec["queries"], fetcher_factory=factory(Replay("govpl")), **kw)
    assert again.new_docs == 0 and again.actions["unchanged"] == 2 and again.evidence_ids == first.evidence_ids
    log = again.to_log()
    assert log["sources"][0]["source"] == "govpl" and len(log["sources"][0]["urls"]) == 2


def test_lookup_reuses_url_already_crawled(conn, embedder, no_wait, keys):
    rec = recorded("factcheck")
    url = json.loads(rec["exchanges"][-1]["body"])["claims"][0]["claimReview"][0]["url"]
    # The crawler stored the same review earlier (crawler source, its own format).
    ingest_corpus(conn, {
        "sources": [{"source_id": "factcheck-demagog", "publisher": "Demagog", "publishing_domain": "demagog.org.pl",
                     "source_type": "factcheck", "citation_url": "https://demagog.org.pl/"}],
        "evidence": [{"source_id": "factcheck-demagog", "upstream_id": url, "kind": "article",
                      "title": "Relokacja do Warszawy? Migranci tu pracują", "citation_url": url,
                      "sections": [{"heading": None, "text": "text: Migranci w Warszawie sprowadzeni w ramach relokacji."}]}],
    }, embedder)
    res = live.live_lookup(conn, embedder, rec["claim"], rec["queries"], fetcher_factory=factory(Replay("factcheck")),
                           sources={"factcheck": live.search_factcheck})
    assert res.reused == 1 and res.new_docs == 0 and len(res.evidence_ids) == 1
    n = conn.execute("SELECT count(*) AS n FROM evidence WHERE source_id LIKE 'live-%%'").fetchone()["n"]
    assert n == 0


def test_lookup_uses_the_trusted_domains_table(conn, embedder, no_wait, keys):
    """The admin panel manages the fact-check domains: a removed domain is not accepted any more."""
    from rag.admin_db import delete_trusted_domain

    rec = recorded("factcheck")
    url = json.loads(rec["exchanges"][-1]["body"])["claims"][0]["claimReview"][0]["url"]
    host = url.split("/")[2].removeprefix("www.")
    trusted = [d for d in live.DEFAULT_TRUSTED_FACTCHECK_DOMAINS if host == d or host.endswith("." + d)]
    assert trusted
    for d in trusted:
        delete_trusted_domain(conn, d)
    res = live.live_lookup(conn, embedder, rec["claim"], rec["queries"], fetcher_factory=factory(Replay("factcheck")),
                           sources={"factcheck": live.search_factcheck})
    assert res.evidence_ids == [] and res.new_docs == 0


# ---- the verifier hook -----------------------------------------------------------------------------


@pytest.fixture
def replay_sources(monkeypatch, no_wait, keys):
    """Live lookup inside Verifier.verify on the recorded gov.pl and EUvsDisinfo answers."""
    replay = Replay("govpl", "euvsdisinfo")
    make = factory(replay)
    monkeypatch.setattr(live, "_shared_fetcher", lambda key: (make(key), threading.Lock()))
    monkeypatch.setattr(live, "SOURCES", {"govpl": live.search_govpl})
    return replay


def test_verifier_answers_from_live_evidence(retriever, replay_sources):
    rec = recorded("govpl")
    c = claim(rec["claim"], rec["queries"])
    calls: list[str] = []

    def policy(cl, xml):
        calls.append(xml)
        ids = [p for p in xml.split('id="')[1:]]
        live_ids = [x.split('"')[0] for x in ids if "Stacja Sanitarno" in x.split(">")[0]]
        if not live_ids:
            return verdict("insufficient")
        return verdict("true", ((live_ids[0], "supports"),))

    v = Verifier(retriever, FakeLLM([c], policy), record=True, live=True)
    res = v.verify(rec["claim"], NOW)
    cr = res.claims[0]
    assert len(calls) == 2  # first pass on the corpus, second on live evidence
    assert cr.verdict == "true"
    assert cr.citations[0].url.startswith("https://www.gov.pl/web/psse-")  # from the stored citation_url
    assert cr.citations[0].publisher.startswith("Powiatowa Stacja Sanitarno-Epidemiologiczna")
    assert any(n.startswith("Wyszukano na żywo w zaufanych źródłach: gov.pl (2)") for n in cr.notes), cr.notes
    assert cr.live and cr.live["new_docs"] == 2
    assert "Wyszukano na żywo" in render_markdown(res)
    logged = json.loads(retriever.conn.execute("SELECT result_json FROM checks").fetchone()["result_json"])
    assert logged["claims"][0]["live"]["sources"][0]["found"] == 2


def test_checkable_unverifiable_is_looked_up_live(retriever, replay_sources):
    """A factual claim the model calls "unverifiable" is treated as insufficient and looked up."""
    rec = recorded("govpl")
    c = claim(rec["claim"], rec["queries"])

    def policy(cl, xml):
        ids = [x.split('"')[0] for x in xml.split('id="')[1:] if "Stacja Sanitarno" in x.split(">")[0]]
        return verdict("true", ((ids[0], "supports"),)) if ids else verdict("unverifiable")

    cr = Verifier(retriever, FakeLLM([c], policy), record=False, live=True).verify(rec["claim"], NOW).claims[0]
    assert replay_sources.seen and cr.live
    assert cr.verdict == "true" and cr.citations[0].url.startswith("https://www.gov.pl/web/psse-")


def test_live_disabled_by_env(retriever, replay_sources, monkeypatch):
    monkeypatch.setenv("PRAWDOWID_LIVE", "0")
    rec = recorded("govpl")
    c = claim(rec["claim"], rec["queries"])
    v = Verifier(retriever, FakeLLM([c], lambda cl, xml: verdict("insufficient")), record=False)
    assert v.live is False
    cr = v.verify(rec["claim"], NOW).claims[0]
    assert cr.verdict == "insufficient" and cr.live is None and not replay_sources.seen


def test_settled_claim_does_not_trigger_live(retriever, replay_sources):
    c = claim("Na Wiśle w Sandomierzu obowiązuje ostrzeżenie hydrologiczne", ["stan wody Wisła Sandomierz"])
    first = Retriever(retriever.conn, retriever.embedder).hybrid([c.text, *c.search_queries], shortlist=8)
    v = Verifier(retriever, FakeLLM([c], lambda cl, xml: verdict("true", ((f"P{first[0].passage_id}", "supports"),))),
                 record=False, live=True)
    cr = v.verify(c.text, NOW).claims[0]
    assert cr.verdict == "true" and cr.live is None and not replay_sources.seen


def test_nothing_found_keeps_first_answer_with_note(retriever, monkeypatch, no_wait):
    replay = Replay()  # every request 404: robots.txt allows, the search fails
    make = factory(replay)
    monkeypatch.setattr(live, "_shared_fetcher", lambda key: (make(key), threading.Lock()))
    monkeypatch.setattr(live, "SOURCES", {"govpl": live.search_govpl})
    c = claim("W Pcimiu bilety autobusowe są tylko w aplikacji", ["Pcim bilety aplikacja"])
    llm = FakeLLM([c], lambda cl, xml: verdict("insufficient"))
    cr = Verifier(retriever, llm, record=False, live=True).verify(c.text, NOW).claims[0]
    assert cr.verdict == "insufficient"
    notes = [n for n in cr.notes if "Wyszukano na żywo" in n]
    assert notes and "Nie wszystkie odpowiedziały: gov.pl" in notes[0]
    assert "Nie znaleziono tam materiałów" not in notes[0]  # the only source failed: no "nothing there"
    assert cr.live["sources"][0]["error"] == "HttpError HTTP 404"


# ---- opinions: fact-check databases only ------------------------------------------------------


def _first_result(verdict_code: str = "unverifiable", known: bool = False):
    from rag.verify import ClaimResult
    return ClaimResult("x", verdict_code, verdict_code, "…", known_disinformation=known)


def test_live_sources_per_claim():
    fact = claim("Od 1 października szczepienia są bezpłatne")
    opinion = claim("Ten rząd jest najgorszy w historii", checkable=False)
    assert live.live_sources(fact, _first_result("insufficient")) == tuple(live.SOURCES)
    assert live.live_sources(fact, _first_result("true")) == ()
    assert live.live_sources(opinion, _first_result("unverifiable")) == ("factcheck", "euvsdisinfo")
    assert live.live_sources(opinion, _first_result("insufficient")) == ("factcheck", "euvsdisinfo")
    # A narrative already documented (code-enforced flag): nothing to look up.
    assert live.live_sources(opinion, _first_result("false", known=True)) == ()
    assert live.live_sources(opinion, _first_result("unverifiable", known=True)) == ()
    assert not live.needs_live(opinion, _first_result("unverifiable", known=True))
    assert "govpl" not in live.OPINION_SOURCES


def test_factual_claims_go_first_and_opinions_ask_no_govpl(monkeypatch):
    """MAX_LIVE_CLAIMS is filled with factual claims first; an opinion-only check asks only the
    fact-check databases."""
    from types import SimpleNamespace

    asked: list[tuple[str, set[str]]] = []

    def fake_lookup(conn, embedder, text, variants, *, sources=None, **kw):
        asked.append((text, set(sources or {})))
        return live.LiveResult()

    monkeypatch.setattr(live, "live_lookup", fake_lookup)
    verifier = SimpleNamespace(retriever=SimpleNamespace(conn=None, embedder=None), shortlist=8, workers=1)
    op1, op2 = claim("Opinia pierwsza", checkable=False), claim("Opinia druga", checkable=False)
    f1, f2 = claim("Fakt pierwszy"), claim("Fakt drugi")
    out = live.live_pass(verifier, [op1, f1, f2], [_first_result(), _first_result("insufficient"),
                                                   _first_result("insufficient")], NOW)
    assert asked[-1] == ("Fakt pierwszy", {"factcheck", "euvsdisinfo", "govpl"})
    assert out[0].live is None and out[1].live and out[2].live  # the opinion lost its slot
    live.live_pass(verifier, [op1, op2], [_first_result(), _first_result()], NOW)
    assert asked[-1] == ("Opinia pierwsza", {"factcheck", "euvsdisinfo"})
    n = len(asked)
    live.live_pass(verifier, [op1], [_first_result("unverifiable", known=True)], NOW)
    assert len(asked) == n  # already a documented narrative: no lookup


@pytest.fixture
def replay_all(monkeypatch, no_wait, keys):
    replay = Replay("govpl", "euvsdisinfo", "factcheck")
    make = factory(replay)
    monkeypatch.setattr(live, "_shared_fetcher", lambda key: (make(key), threading.Lock()))
    return replay


def _arms_case(xml: str) -> str | None:
    for x in xml.split('id="')[1:]:
        if "EUvsDisinfo (Europejska" in x and "Ukrainian arms flood into Poland" in x:
            return x.split('"')[0]
    return None


def test_opinion_looks_up_factcheck_databases_and_gets_the_flag(retriever, replay_all):
    rec = recorded("euvsdisinfo")
    c = claim(rec["claim"], rec["queries"], checkable=False)
    calls: list[str] = []

    def policy(cl, xml):
        calls.append(xml)
        case = _arms_case(xml)
        if not case:
            return verdict("unverifiable")
        return verdict("false", ((case, "contradicts"),), known_disinformation=True)

    cr = Verifier(retriever, FakeLLM([c], policy), record=False, live=True).verify(rec["claim"], NOW).claims[0]
    hosts = {r.url.host for r in replay_all.seen}
    assert "www.gov.pl" not in hosts
    assert {"mcp.euvsd.info", "factchecktools.googleapis.com"} <= hosts
    assert [s["source"] for s in cr.live["sources"]] == ["factcheck", "euvsdisinfo"]
    assert len(calls) == 2
    assert cr.verdict == "false" and cr.known_disinformation
    assert cr.citations[0].url == "https://euvsdisinfo.eu/report/ukrainian-arms-flood-into-poland"
    assert cr.notes[0].startswith("Znana narracja dezinformacyjna udokumentowana przez: EUvsDisinfo")


def test_opinion_without_a_cited_debunk_keeps_its_first_answer(retriever, replay_all):
    """Live passages alone cannot give an opinion a verdict: without the code-enforced flag the
    first answer stays (here the model calls it true on the quoted narrative, with no debunk)."""
    rec = recorded("euvsdisinfo")
    c = claim(rec["claim"], rec["queries"], checkable=False)

    def policy(cl, xml):
        case = _arms_case(xml)
        return verdict("true", ((case, "supports"),)) if case else verdict("unverifiable")

    cr = Verifier(retriever, FakeLLM([c], policy), record=False, live=True).verify(rec["claim"], NOW).claims[0]
    assert cr.live and "www.gov.pl" not in {r.url.host for r in replay_all.seen}
    assert cr.verdict == "unverifiable" and not cr.known_disinformation and not cr.citations
    assert any(n.startswith("Wyszukano na żywo") for n in cr.notes)


# ---- small helpers ---------------------------------------------------------------------------


def test_utc_converts_to_utc():
    assert live._utc("2026-11-20T10:00:00") == "2026-11-20T09:00:00Z"  # naive: Warsaw (CET)
    assert live._utc("2026-07-01T12:00:00") == "2026-07-01T10:00:00Z"  # Warsaw summer time
    assert live._utc("2026-11-20T10:00:00+02:00") == "2026-11-20T08:00:00Z"
    assert live._utc("2026-10-05T06:25:26.082973Z") == "2026-10-05T06:25:26Z"
    assert live._utc(None) is None and live._utc("wczoraj") is None


def test_euvsdisinfo_case_date_becomes_published_at():
    from crawler.fetcher import Resp
    from crawler.mcp import tool_text

    ex = next(e for e in recorded("euvsdisinfo")["exchanges"] if e["method"] == "POST")
    text = tool_text(Resp(url="", final_url="", status=200, text=ex["body"]))
    url = "https://euvsdisinfo.eu/report/ukrainian-arms-flood-into-poland"
    # The recorded `search` answer has no date: nothing is made up.
    plain = [i for i in live.parse_euvsd_search(text) if i["url"] == url]
    assert live.euvsd_records(plain, "2026-11-22T00:00:00Z")[0]["published_at"] is None
    # A `**Date:**` line (format of the server's `cases` tool) fills published_at, not the text.
    dated = text.replace(f"**Source URL:** {url}", f"**Date:** 11/16/2026\n\n**Source URL:** {url}")
    item = next(i for i in live.parse_euvsd_search(dated) if i["url"] == url)
    assert "Date" not in item["content"] and item["content"].rstrip().endswith("RESPONSE")
    assert live.euvsd_records([item], "2026-11-22T00:00:00Z")[0]["published_at"] == "2026-11-16T00:00:00Z"
