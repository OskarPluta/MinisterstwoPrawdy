"""Schema v5 and admin-panel storage: migration, outcomes, alerts, source controls, withdrawal,
trusted domains, per-check usage (no network, scripted LLM)."""

from __future__ import annotations

import copy
import json
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg
import pytest

from rag import admin_db
from rag.alerts import acknowledge, list_alerts, raise_alert
from rag.db import SCHEMA_VERSION, _identifier_sql, connect, corpus_version, list_sources, trusted_domains
from rag.ingest import ingest_corpus
from rag.llm import Claim, ClaimExtraction, ClaimVerdict, LLMRefusal, PassageLabel, Usage
from rag.outcomes import classify, usage_columns
from rag.search import Retriever, passage_meta
from rag.trusted import DEFAULT_TRUSTED_FACTCHECK_DOMAINS
from rag.verify import Verifier, record_failure

NOW = datetime(2026, 11, 21, 12, 0, tzinfo=ZoneInfo("Europe/Warsaw"))
FIXTURES = Path(__file__).resolve().parent / "fixtures"
HYDRO_QUERY = "ostrzeżenie hydrologiczne Wisła Sandomierz"


def _claim(text: str) -> Claim:
    return Claim.model_validate({
        "text": text, "search_queries": [text], "entities": [], "area": None,
        "time_from": None, "time_to": None, "quantifiers": [], "checkable": True,
    })


class ScriptedLLM:
    """One claim per extraction; verify cites the first passage, or raises `verify_error`."""

    def __init__(self, *, model: str = "gpt-5.5", extract_error: Exception | None = None,
                 verify_error: Exception | None = None, claims: int = 1):
        self.model = model
        self.usage = Usage()
        self.extract_error = extract_error
        self.verify_error = verify_error
        self.n = claims

    def extract_claims(self, text: str, now: datetime, attachments_xml: str = "") -> ClaimExtraction:
        self.usage.add("extract", 1000, 100)
        if self.extract_error:
            raise self.extract_error
        claims = [_claim("Na Wiśle w Sandomierzu obowiązuje ostrzeżenie hydrologiczne")]
        claims += [_claim(f"W Sandomierzu stan wody Wisły przekroczył stan ostrzegawczy {i}") for i in range(self.n - 1)]
        return ClaimExtraction(status="ok", clarification_question=None, claims=claims, injection_detected=False)

    def verify_claim(self, claim: Claim, evidence_xml: str, now: datetime) -> ClaimVerdict:
        self.usage.add("verify", 2000, 300)
        if self.verify_error and claim.text.startswith("W Sandomierzu"):
            raise self.verify_error
        if self.verify_error and self.n == 1:
            raise self.verify_error
        first = evidence_xml.split('id="', 1)[1].split('"', 1)[0]
        return ClaimVerdict(verdict="true", passages=[PassageLabel(passage_id=first, role="supports")],
                            explanation_pl="Potwierdzone.", evidence_strength="strong", conflict=False,
                            injection_noticed=False)


class APITimeoutError(Exception):
    """Same class name as the OpenAI / Anthropic SDK timeout."""


def _check_row(conn, check_id: str) -> dict:
    row = admin_db.get_check(conn, check_id)
    assert row is not None
    return row


# ---- migration ---------------------------------------------------------------------------

def test_v1_database_is_migrated_and_backfilled(database_url):
    name = f"scratch_{uuid.uuid4().hex[:12]}"
    raw = psycopg.connect(database_url, autocommit=True)
    try:
        raw.execute(f'CREATE SCHEMA "{name}"')
        raw.execute("SELECT set_config('search_path', %s, false)", (name,))
        raw.execute((FIXTURES / "schema_v1.sql").read_text(encoding="utf-8"))
        raw.execute("INSERT INTO meta (key, value) VALUES ('schema_version', '1')")
        result = {"status": "error", "error": "APITimeoutError('Request timed out.')", "claims": [],
                  "usage": {"calls": 1, "input_tokens": 1000, "output_tokens": 0, "cost_usd": None}}
        raw.execute(
            "INSERT INTO checks (check_id, created_at, input_masked, status, result_json, corpus_version, model) "
            "VALUES ('old-1', '2026-11-20T10:00:00Z', 'x', 'error', %s, 0, 'GLM-5.3-Flash')",
            (json.dumps(result),),
        )
    finally:
        raw.close()

    conn = connect(database_url, db_schema=name)
    try:
        assert conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()["value"] == str(SCHEMA_VERSION)
        # The steps between v1 and v5 ran too: licences (v2), share key (v3), reprints (v4).
        conn.execute("SELECT license, attribution, processed_notice FROM sources LIMIT 0")
        conn.execute("SELECT share_id, shared_at FROM checks LIMIT 0")
        conn.execute("SELECT reprint_of FROM reprint_signatures LIMIT 0")
        row = _check_row(conn, "old-1")
        assert row["outcome"] == "llm_timeout" and row["error_type"] == "APITimeoutError"
        # PCSS model priced in PLN: the cost the old row could not store in USD is now derived.
        assert row["cost_currency"] == "PLN" and row["cost"] == pytest.approx(1000 * 2.948 / 1e6)
        assert set(trusted_domains(conn)) == set(DEFAULT_TRUSTED_FACTCHECK_DOMAINS)
    finally:
        conn.execute(_identifier_sql("meta.drop_schema", name))
        conn.close()


def test_trusted_domains_seeded_once(new_db, database_url):
    c = new_db()
    assert "oko.press" in trusted_domains(c)
    assert admin_db.delete_trusted_domain(c, "oko.press")
    assert admin_db.add_trusted_domain(c, "https://www.Example-Factcheck.pl/o-nas") == "example-factcheck.pl"
    schema = c.execute("SELECT current_schema() AS s").fetchone()["s"]
    again = connect(database_url, db_schema=schema)
    try:
        domains = trusted_domains(again)
        assert "oko.press" not in domains and "example-factcheck.pl" in domains
    finally:
        again.close()
    with pytest.raises(ValueError):
        admin_db.add_trusted_domain(c, "not a domain")


# ---- outcomes ----------------------------------------------------------------------------

@pytest.mark.parametrize(("payload", "expected"), [
    ({"status": "error", "error_type": "LLMRefusal"}, ("llm_refusal", "LLMRefusal")),
    ({"status": "error", "error": "RateLimitError('429')"}, ("llm_rate_limit", "RateLimitError")),
    ({"status": "error", "error": "openai.AuthenticationError('bad key')"}, ("llm_auth", "AuthenticationError")),
    ({"status": "error", "error": "OperationalError('db down')"}, ("internal_error", "OperationalError")),
    ({"status": "error", "error": "ValueError('x')"}, ("llm_error", "ValueError")),
    ({"status": "unreadable"}, ("unreadable", None)),
    ({"status": "ok", "claims": [{"verdict": "true"}, {"verdict": "error", "error": "APITimeoutError()"}]},
     ("partial_error", "APITimeoutError")),
    ({"status": "ok", "claims": [{"verdict": "insufficient", "live": {"sources": [
        {"source": "govpl", "error": "HttpError HTTP 503"}]}}]}, ("live_error", "HttpError")),
    ({"status": "ok", "claims": [{"verdict": "insufficient", "live": {"sources": [
        {"source": "factcheck", "error": None, "skipped": "brak klucza"}]}}]}, ("no_evidence", None)),
    ({"status": "ok", "claims": [{"verdict": "misleading"}]}, ("verdict", None)),
])
def test_classify(payload, expected):
    assert classify(payload) == expected


def test_usage_columns_keep_the_model_currency():
    cols = usage_columns({"usage": {"calls": 2, "input_tokens": 10, "output_tokens": 5, "cost": 0.5,
                                    "currency": "PLN"}}, "GLM-5.3-Flash")
    assert cols == {"llm_calls": 2, "input_tokens": 10, "output_tokens": 5, "cost": 0.5, "cost_currency": "PLN"}
    assert usage_columns({}, "gpt-5.5")["cost"] is None


# ---- checks log: outcome, channel, usage, alerts ---------------------------------------------

def test_verdict_check_is_logged_with_channel_usage_and_no_alert(retriever, monkeypatch):
    monkeypatch.delenv("PRAWDOWID_PRICE_IN", raising=False)
    monkeypatch.delenv("PRAWDOWID_PRICE_OUT", raising=False)
    v = Verifier(retriever, ScriptedLLM(model="GLM-5.3-Flash"), live=False, channel="discord")
    res = v.verify("Czy na Wiśle w Sandomierzu jest ostrzeżenie?", NOW)
    row = _check_row(retriever.conn, res.check_id)
    assert row["channel"] == "discord" and row["outcome"] == "verdict"
    assert row["llm_calls"] == 2 and row["input_tokens"] == 3000 and row["output_tokens"] == 400
    assert row["cost_currency"] == "PLN"
    assert row["cost"] == pytest.approx((3000 * 2.948 + 400 * 5.895) / 1e6, abs=1e-6)
    assert [c["call"] for c in row["result"]["usage"]["by_call"]] == ["extract", "verify"]
    assert res.usage and res.usage["cost_usd"] is None  # not a USD price
    assert list_alerts(retriever.conn) == []


def test_failures_are_categorised_and_raise_deduplicated_alerts(retriever):
    conn = retriever.conn
    v = Verifier(retriever, ScriptedLLM(extract_error=APITimeoutError("timed out")), live=False)
    r1 = v.verify("Czy na Wiśle jest ostrzeżenie? mój mail jan@example.pl", NOW)
    r2 = v.verify("Drugi raz", NOW)
    assert _check_row(conn, r1.check_id)["outcome"] == "llm_timeout"
    alerts = list_alerts(conn, open_only=True)
    assert len(alerts) == 1
    a = alerts[0]
    assert a["count"] == 2 and a["severity"] == "error" and a["check_id"] == r2.check_id
    assert a["fingerprint"] == "check:llm_timeout:APITimeoutError"
    assert "jan@example.pl" not in json.dumps(a, default=str) and "Wiśle" not in a["message"]

    refused = Verifier(retriever, ScriptedLLM(extract_error=LLMRefusal("no")), live=False).verify("x", NOW)
    assert _check_row(conn, refused.check_id)["outcome"] == "llm_refusal"
    assert {x["fingerprint"]: x["severity"] for x in list_alerts(conn)}["check:llm_refusal:LLMRefusal"] == "warning"

    partial = Verifier(retriever, ScriptedLLM(verify_error=APITimeoutError("slow"), claims=2), live=False)
    pr = partial.verify("Dwa twierdzenia o Wiśle", NOW)
    row = _check_row(conn, pr.check_id)
    assert pr.status == "ok" and row["outcome"] == "partial_error" and row["error_type"] == "APITimeoutError"


def test_record_failure_logs_masked_input_and_alert(new_db):
    c = new_db()
    res = record_failure(c, "Zadzwoń 600 700 800", SystemExit("PCSS_API_KEY is not set"),
                         outcome="config_error", channel="web")
    row = _check_row(c, res.check_id)
    assert row["outcome"] == "config_error" and row["error_type"] == "SystemExit" and row["channel"] == "web"
    assert "600 700 800" not in row["input_masked"] and "[telefon]" in row["input_masked"]
    assert list_alerts(c, open_only=True)[0]["fingerprint"] == "check:config_error:SystemExit"


def test_record_failure_respects_no_check_log(new_db, monkeypatch):
    c = new_db()
    monkeypatch.setenv("PRAWDOWID_CHECKS_RETENTION_DAYS", "0")
    res = record_failure(c, "x", TimeoutError("t"), outcome="llm_timeout", channel="web")
    assert admin_db.get_check(c, res.check_id) is None
    assert list_alerts(c) == []


def test_deleting_a_reviewed_check_deletes_its_review(new_db):
    from rag.db import delete_checks

    c = new_db()
    res = record_failure(c, "x", TimeoutError("t"), outcome="llm_timeout", channel="web")
    admin_db.set_review(c, res.check_id, "llm_timeout", None)
    assert delete_checks(c, [res.check_id]) == 1, "retention, /usun and purge delete reviewed checks too"
    assert admin_db.get_check(c, res.check_id) is None


def test_alert_dedupe_ack_and_once(new_db):
    c = new_db()
    a1 = raise_alert(c, kind="rule", severity="error", fingerprint="f", message="m")
    a2 = raise_alert(c, kind="rule", severity="error", fingerprint="f", message="m2")
    assert a1 == a2 and list_alerts(c)[0]["count"] == 2
    assert acknowledge(c, a1)
    a3 = raise_alert(c, kind="rule", severity="error", fingerprint="f", message="m")
    assert a3 != a1 and len(list_alerts(c, open_only=True)) == 1
    assert raise_alert(c, kind="rule", severity="warning", fingerprint="g", message="m", once=True) is not None
    acknowledge(c, list_alerts(c, open_only=True)[0]["alert_id"])
    assert raise_alert(c, kind="rule", severity="warning", fingerprint="g", message="m", once=True) is None


def test_checks_page_filters_and_cursor(retriever):
    v = Verifier(retriever, ScriptedLLM(), live=False, channel="web")
    ids = [v.verify(f"Czy na Wiśle jest ostrzeżenie? {i}", NOW).check_id for i in range(3)]
    bad = Verifier(retriever, ScriptedLLM(extract_error=APITimeoutError("x")), live=False, channel="discord")
    bad_id = bad.verify("awaria", NOW).check_id
    conn = retriever.conn
    page, cursor = admin_db.checks_page(conn, limit=2)
    assert len(page) == 2 and cursor
    rest, end = admin_db.checks_page(conn, limit=2, cursor=cursor)
    assert end is None and {r["check_id"] for r in page + rest} == {*ids, bad_id}
    failed, _ = admin_db.checks_page(conn, outcomes=["llm_timeout"])
    assert [r["check_id"] for r in failed] == [bad_id]
    assert [r["check_id"] for r in admin_db.checks_page(conn, channel="discord")[0]] == [bad_id]
    assert [r["check_id"] for r in admin_db.checks_page(conn, q="AWARIA")[0]] == [bad_id]
    admin_db.set_review(conn, bad_id, "awaria dostawcy", "PCSS nie odpowiadał")
    assert bad_id not in {r["check_id"] for r in admin_db.checks_page(conn, unreviewed=True)[0]}
    assert _check_row(conn, bad_id)["review_label"] == "awaria dostawcy"


# ---- sources: enable/disable, overrides ------------------------------------------------------

def _hydro_source(retriever: Retriever) -> tuple[str, list[int]]:
    hits = retriever.hybrid(HYDRO_QUERY)
    sid = hits[0].meta["source_id"]
    return sid, [h.passage_id for h in hits if h.meta["source_id"] == sid]


def test_disabled_source_leaves_search_and_overrides_survive_ingest(retriever, corpus, embedder):
    conn = retriever.conn
    sid, ids = _hydro_source(retriever)
    v0 = corpus_version(conn)
    admin_db.update_source(conn, sid, enabled=False, publisher=None, source_type=None, note="test")
    assert corpus_version(conn) > v0
    meta = passage_meta(conn, retriever.bm25(HYDRO_QUERY, 50) + retriever.dense(HYDRO_QUERY, 50))
    assert all(m["source_id"] != sid for m in meta.values())

    admin_db.update_source(conn, sid, enabled=True, publisher="Instytut (zmieniony)", source_type="secondary",
                           note=None)
    ingest_corpus(conn, corpus, embedder)  # a reload must not undo the admin's changes
    src = {s["source_id"]: s for s in list_sources(conn)}[sid]
    assert src["publisher"] == "Instytut (zmieniony)" and src["source_type"] == "secondary" and src["enabled"]
    assert passage_meta(conn, ids)[ids[0]]["publisher"] == "Instytut (zmieniony)"
    with pytest.raises(ValueError):
        admin_db.update_source(conn, sid, enabled=True, publisher=None, source_type="blog", note=None)


def test_withdrawn_record_is_not_revived_by_ingest_and_can_be_restored(retriever, corpus, embedder):
    conn = retriever.conn
    sid, ids = _hydro_source(retriever)
    eid = passage_meta(conn, ids)[ids[0]]["evidence_id"]
    ev = admin_db.withdraw_evidence(conn, eid, "nieaktualne")
    assert ev and ev["withdrawn_at"] and not ev["is_current"]
    assert eid not in {passage_meta(conn, [p])[p]["evidence_id"] for p in retriever.bm25(HYDRO_QUERY, 50)}

    changed = copy.deepcopy(corpus)
    rec = next(r for r in changed["evidence"] if r["source_id"] == sid and r["upstream_id"] == ev["upstream_id"])
    rec["title"] += " (aktualizacja)"
    stats = ingest_corpus(conn, changed, embedder)
    assert stats["withdrawn"] == 1 and stats["new_version"] == 0
    assert [d for d in admin_db.source_documents(conn, sid) if d["upstream_id"] == ev["upstream_id"]][0]["withdrawn_at"]

    restored = admin_db.restore_evidence(conn, eid)
    assert restored and restored["is_current"] and restored["withdrawn_at"] is None
    assert any(passage_meta(conn, [p])[p]["evidence_id"] == eid for p in retriever.bm25(HYDRO_QUERY, 50))
    assert any(passage_meta(conn, [p])[p]["evidence_id"] == eid for p in retriever.dense(HYDRO_QUERY, 50))


def test_create_manual_source(new_db):
    c = new_db()
    src = admin_db.create_source(c, source_id="admin-gus", publisher="GUS", domain="https://stat.gov.pl/",
                                 source_type="official", note="ręcznie")
    assert src["publishing_domain"] == "stat.gov.pl" and src["admin_note"] == "ręcznie"
    with pytest.raises(ValueError):
        admin_db.create_source(c, source_id="admin-gus", publisher="GUS", domain="stat.gov.pl",
                               source_type="official")
