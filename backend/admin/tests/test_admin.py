"""Admin API tests: TestClient over the fake corpus, a scripted LLM and a mocked web (no network).

Fixtures live here, not in a conftest.py (a top-level `conftest` module already comes from
backend/rag/tests and pytest would refuse a second one).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient

from admin.app import Services, create_app
from admin.auth import COOKIE, CSRF_HEADER, CSRF_VALUE, AuthConfig
from admin.notifier import Notifier, NotifierConfig
from intake.fetch import SafeFetcher
from rag.alerts import list_alerts, raise_alert
from rag.config import load_env
from rag.db import corpus_version, scratch_db
from rag.embed import HashEmbedder
from rag.ingest import ingest_corpus, load_corpus
from rag.llm import Claim, ClaimExtraction, ClaimVerdict, PassageLabel, Usage
from rag.search import Retriever
from rag.verify import Verifier, record_failure

FIXTURES = Path(__file__).resolve().parents[2] / "rag" / "tests" / "fixtures"
load_env()
os.environ["PRAWDOWID_LIVE"] = "0"
PASSWORD = "correct horse battery"
H = {CSRF_HEADER: CSRF_VALUE}
NOW = datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc)

ARTICLE = """<html><head><title>Komunikat o stanie wód</title></head><body><article>
<h1>Komunikat o stanie wód</h1><p>{body}</p></article></body></html>"""
ARTICLE_TEXT = ("Wojewódzkie centrum informuje, że stan wody na Wiśle w Sandomierzu spadł poniżej stanu "
                "ostrzegawczego. Mieszkańcy nadbrzeżnych ulic mogą wrócić do domów. ") * 4


def _claim(text: str) -> Claim:
    return Claim.model_validate({
        "text": text, "search_queries": [text], "entities": [], "area": None,
        "time_from": None, "time_to": None, "quantifiers": [], "checkable": True,
    })


class FakeLLM:
    model = "GLM-5.3-Flash"

    def __init__(self) -> None:
        self.usage = Usage()

    def extract_claims(self, text: str, now: datetime, attachments_xml: str = "") -> ClaimExtraction:
        self.usage.add("extract", 500, 100)
        return ClaimExtraction(status="ok", clarification_question=None, injection_detected=False,
                               claims=[_claim("Na Wiśle w Sandomierzu obowiązuje ostrzeżenie hydrologiczne")])

    def verify_claim(self, claim: Claim, evidence_xml: str, now: datetime) -> ClaimVerdict:
        self.usage.add("verify", 1500, 200)
        first = evidence_xml.split('id="', 1)[1].split('"', 1)[0]
        return ClaimVerdict(verdict="true", passages=[PassageLabel(passage_id=first, role="supports")],
                            explanation_pl="Potwierdzone.", evidence_strength="strong", conflict=False,
                            injection_noticed=False)


def web(request: httpx.Request) -> httpx.Response:
    """The mocked web: wiadomosci.test (robots.txt disallows /prywatne/)."""
    path = request.url.path
    if path == "/robots.txt":
        return httpx.Response(200, text="User-agent: *\nDisallow: /prywatne/\n")
    if path.startswith("/artykul/"):
        return httpx.Response(200, html=ARTICLE.format(body=ARTICLE_TEXT))
    if path == "/przekierowanie":
        return httpx.Response(302, headers={"location": "https://inna.test/artykul/1"})
    return httpx.Response(404, text="nie ma")


def fake_fetcher() -> SafeFetcher:
    table = {"wiadomosci.test": ["93.184.216.34"], "inna.test": ["93.184.216.35"], "wewnetrzna.test": ["10.0.0.7"]}
    return SafeFetcher("FaktBot/test (+t@example.test)", min_delay=0, transport=httpx.MockTransport(web),
                       resolver=lambda host, port: table[host])


@pytest.fixture
def env():
    url = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("no TEST_DATABASE_URL / DATABASE_URL set (see .env.example)")
    try:
        psycopg.connect(url, connect_timeout=3).close()
    except psycopg.OperationalError as e:
        pytest.skip(f"PostgreSQL not reachable: {e}".splitlines()[0])
    with scratch_db(url) as conn:
        embedder = HashEmbedder()
        ingest_corpus(conn, load_corpus(FIXTURES / "fake_corpus.json"), embedder)
        svc = Services(conn=conn, embedder_factory=lambda: embedder, fetcher_factory=fake_fetcher,
                       auth=AuthConfig(PASSWORD, b"x" * 32), owns_conn=False)
        with TestClient(create_app(lambda: svc)) as c:
            yield c, conn, embedder


@pytest.fixture
def client(env):
    c, _, _ = env
    assert c.post("/admin/api/login", json={"password": PASSWORD}).status_code == 204
    return c


def _verifier(conn, embedder, channel: str = "web") -> Verifier:
    return Verifier(Retriever(conn, embedder), FakeLLM(), live=False, channel=channel)


# ---- auth ----------------------------------------------------------------------------------

def test_login_logout_and_csrf(env):
    c, _, _ = env
    assert c.get("/admin/api/overview").status_code == 401
    assert c.post("/admin/api/login", json={"password": "zle"}).status_code == 401
    r = c.post("/admin/api/login", json={"password": PASSWORD})
    assert r.status_code == 204
    cookie = r.headers["set-cookie"]
    assert COOKIE in cookie and "HttpOnly" in cookie and "SameSite=strict" in cookie
    assert c.get("/admin/api/me").json()["ok"] is True
    # A change without the panel's header is refused even with a valid session.
    assert c.post("/admin/api/alerts/ack-all").status_code == 403
    assert c.post("/admin/api/alerts/ack-all", headers=H).status_code == 200
    c.post("/admin/api/logout")
    assert c.get("/admin/api/me").status_code == 401


def test_forged_or_expired_token_is_refused():
    cfg = AuthConfig(PASSWORD, b"s" * 32, ttl_s=60)
    t = cfg.make_token(now=1000)
    assert cfg.token_expiry(t, now=1001) == 1060
    assert cfg.token_expiry(t, now=2000) is None
    exp, nonce, sig = t.split(".")
    assert cfg.token_expiry(f"{int(exp) + 9999}.{nonce}.{sig}", now=1001) is None
    assert AuthConfig(PASSWORD, b"o" * 32).token_expiry(t, now=1001) is None


def test_login_is_throttled(env):
    c, _, _ = env
    for _ in range(5):
        assert c.post("/admin/api/login", json={"password": "zle"}).status_code == 401
    assert c.post("/admin/api/login", json={"password": PASSWORD}).status_code == 429


def test_missing_password_refuses_to_start(monkeypatch):
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    with pytest.raises(SystemExit, match="ADMIN_PASSWORD"):
        AuthConfig.from_env()
    monkeypatch.setenv("ADMIN_PASSWORD", PASSWORD)
    monkeypatch.setenv("ADMIN_SECRET", "short")
    with pytest.raises(SystemExit, match="ADMIN_SECRET"):
        AuthConfig.from_env()


# ---- history, failures, usage --------------------------------------------------------------

def test_history_detail_review_and_audit(client, env):
    _, conn, embedder = env
    ok = _verifier(conn, embedder).verify("Czy na Wiśle w Sandomierzu jest ostrzeżenie? tel. 600 700 800")
    bad = record_failure(conn, "Mail: jan@example.pl", SystemExit("no key"), outcome="config_error", channel="discord")

    items = client.get("/admin/api/checks").json()["items"]
    assert {i["check_id"] for i in items} == {ok.check_id, bad.check_id}
    assert all("600 700 800" not in i["input_masked"] and "jan@example.pl" not in i["input_masked"] for i in items)
    failed = client.get("/admin/api/checks", params={"group": "failure"}).json()["items"]
    assert [i["check_id"] for i in failed] == [bad.check_id] and failed[0]["outcome_pl"] == "błąd konfiguracji"
    assert [i["check_id"] for i in client.get("/admin/api/checks", params={"channel": "discord"}).json()["items"]] \
        == [bad.check_id]

    d = client.get(f"/admin/api/checks/{ok.check_id}").json()
    assert d["outcome"] == "verdict" and d["group"] == "success" and d["cost_currency"] == "PLN"
    assert d["result"]["usage"]["by_call"] and d["review"] is None
    assert client.get("/admin/api/checks/nie-ma").status_code == 404

    assert client.put(f"/admin/api/checks/{bad.check_id}/review", json={"label": "brak klucza"}, headers=H).status_code == 200
    assert client.get(f"/admin/api/checks/{bad.check_id}").json()["review"]["label"] == "brak klucza"
    assert client.get("/admin/api/checks", params={"group": "failure", "unreviewed": True}).json()["items"] == []
    actions = [a["action"] for a in client.get("/admin/api/audit").json()["items"]]
    assert "check.review" in actions and "login" in actions


def test_overview_failures_and_usage(client, env):
    _, conn, embedder = env
    v = _verifier(conn, embedder)
    for i in range(2):
        v.verify(f"Czy na Wiśle jest ostrzeżenie? {i}")
    record_failure(conn, "x", TimeoutError("t"), outcome="llm_timeout", channel="web")

    ov = client.get("/admin/api/overview").json()
    w = ov["windows"]["24h"]
    assert w["total"] == 3 and w["by_group"]["failure"] == 1 and w["failure_rate"] == pytest.approx(1 / 3, abs=1e-4)
    assert set(w["cost"]) == {"PLN"} and w["calls"] == 4
    assert len(ov["daily"]) == 14 and ov["daily"][-1]["total"] == 3
    assert ov["alerts"]["open"] == 1 and ov["sources"]["total"] > 0

    f = client.get("/admin/api/failures").json()
    assert f["by_outcome"] == [{"outcome": "llm_timeout", "label": "LLM: przekroczony czas", "group": "failure", "count": 1}]
    assert f["unreviewed"] == 1 and f["error_types"][0]["error_type"] == "TimeoutError"

    u = client.get("/admin/api/usage", params={"group": "call"}).json()
    by = {r["key"]: r for r in u["rows"]}
    assert by["extract"]["calls"] == 2 and by["verify"]["input_tokens"] == 3000 and "PLN" in by["verify"]["cost"]
    days = client.get("/admin/api/usage").json()
    assert len(days["rows"]) == 30 and days["totals"]["checks"] == 3
    assert client.get("/admin/api/usage", params={"group": "nope"}).status_code == 422
    assert client.get("/admin/api/usage", params={"from": "2026-13-01"}).status_code == 422


# ---- sources, links, withdrawal, domains -----------------------------------------------------

def test_source_toggle_and_validation(client, env):
    _, conn, _ = env
    sid = client.get("/admin/api/sources").json()["items"][0]["source_id"]
    v0 = corpus_version(conn)
    r = client.patch(f"/admin/api/sources/{sid}", json={"enabled": False, "note": "przegląd"}, headers=H)
    assert r.status_code == 200 and r.json()["enabled"] is False and r.json()["admin_note"] == "przegląd"
    assert corpus_version(conn) > v0
    assert client.patch(f"/admin/api/sources/{sid}", json={"enabled": True, "source_type": "blog"}, headers=H).status_code == 422
    assert client.patch("/admin/api/sources/nie-ma", json={"enabled": True}, headers=H).status_code == 404


def test_add_link_withdraw_and_restore(client):
    body = {"source_id": "admin-wiadomosci", "publisher": "Wiadomości Testowe", "domain": "wiadomosci.test",
            "source_type": "official"}
    assert client.post("/admin/api/sources", json=body, headers=H).status_code == 201

    url = "/admin/api/sources/admin-wiadomosci/documents"
    r = client.post(url, json={"url": "https://wiadomosci.test/artykul/1#gora"}, headers=H)
    assert r.status_code == 201, r.text
    added = r.json()
    assert added["action"] == "inserted" and added["citation_url"] == "https://wiadomosci.test/artykul/1"
    assert client.post(url, json={"url": "https://wiadomosci.test/artykul/1"}, headers=H).json()["action"] == "unchanged"

    def refused(link: str) -> str:
        res = client.post(url, json={"url": link}, headers=H)
        assert res.status_code == 422
        return res.json()["detail"]

    assert "robots.txt" in refused("https://wiadomosci.test/prywatne/1")
    assert "domeny źródła" in refused("https://inna.test/artykul/1")
    assert "domeny źródła" in refused("http://wiadomosci.test/artykul/1")
    assert "poza domenę" in refused("https://wiadomosci.test/przekierowanie")

    docs = client.get(url).json()["items"]
    assert [d["citation_url"] for d in docs] == ["https://wiadomosci.test/artykul/1"]
    eid = docs[0]["evidence_id"]
    w = client.post(f"/admin/api/documents/{eid}/withdraw", json={"note": "duplikat"}, headers=H).json()
    assert w["withdrawn_at"] and not w["is_current"]
    assert "wycofany" in refused("https://wiadomosci.test/artykul/1")
    restored = client.post(f"/admin/api/documents/{eid}/restore", headers=H).json()
    assert restored["is_current"] and restored["withdrawn_at"] is None
    assert client.post(f"/admin/api/documents/{eid}/restore", headers=H).status_code == 404


def test_private_address_is_never_fetched(client):
    body = {"source_id": "admin-wewn", "publisher": "Wewnętrzna", "domain": "wewnetrzna.test", "source_type": "official"}
    client.post("/admin/api/sources", json=body, headers=H)
    r = client.post("/admin/api/sources/admin-wewn/documents", json={"url": "https://wewnetrzna.test/artykul/1"}, headers=H)
    assert r.status_code == 422 and "sieci wewnętrznej" in r.json()["detail"]


def test_trusted_domains(client):
    items = client.get("/admin/api/trusted-domains").json()["items"]
    assert any(d["domain"] == "demagog.org.pl" for d in items)
    assert client.post("/admin/api/trusted-domains", json={"domain": "https://www.nowy-fc.pl/"}, headers=H).json() \
        == {"domain": "nowy-fc.pl"}
    assert client.post("/admin/api/trusted-domains", json={"domain": "nie domena"}, headers=H).status_code == 422
    assert client.delete("/admin/api/trusted-domains/demagog.org.pl", headers=H).status_code == 204
    assert client.delete("/admin/api/trusted-domains/demagog.org.pl", headers=H).status_code == 404
    names = {d["domain"] for d in client.get("/admin/api/trusted-domains").json()["items"]}
    assert "nowy-fc.pl" in names and "demagog.org.pl" not in names


# ---- alerts and the notifier -----------------------------------------------------------------

def test_alert_list_and_ack(client, env):
    _, conn, _ = env
    a = raise_alert(conn, kind="rule", severity="error", fingerprint="t", message="Test")
    assert client.get("/admin/api/alerts/count").json() == {"open": 1, "errors": 1}
    assert client.post(f"/admin/api/alerts/{a}/ack", headers=H).json() == {"open": 0, "errors": 0}
    assert client.post(f"/admin/api/alerts/{a}/ack", headers=H).status_code == 404
    assert client.get("/admin/api/alerts").json()["items"][0]["acknowledged_at"]


class Hook:
    """Mock Discord webhook: records payloads, answers with the queued status codes."""

    def __init__(self, *codes: int):
        self.codes = list(codes)
        self.sent: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.sent.append(json.loads(request.content))
        code = self.codes.pop(0) if self.codes else 204
        return httpx.Response(code, json={"retry_after": 0.01} if code == 429 else None)


def test_notifier_sends_without_user_text_and_honours_429(env):
    _, conn, _ = env
    record_failure(conn, "Mój PESEL 90010112345 i jan@example.pl", TimeoutError("x"), outcome="llm_timeout",
                   channel="web")
    hook = Hook(429)
    clock = [NOW]
    n = Notifier(NotifierConfig(webhook_url="https://discord.test/hook", public_url="https://admin.test"),
                 transport=httpx.MockTransport(hook), clock=lambda: clock[0])
    # send_pending only (the fake corpus has a source with a fetch error, which a rule would report)
    assert n.send_pending(conn) == 0  # 429: nothing marked as sent
    n._backoff_until = 0
    assert n.send_pending(conn) == 1
    text = hook.sent[-1]["content"]
    assert "LLM: przekroczony czas" in text and "https://admin.test/historia?id=" in text
    assert "90010112345" not in text and "jan@example.pl" not in text and "PESEL" not in text
    assert hook.sent[-1]["allowed_mentions"] == {"parse": []}
    assert n.send_pending(conn) == 0  # already sent, no repeat

    record_failure(conn, "again", TimeoutError("x"), outcome="llm_timeout", channel="web")
    assert n.send_pending(conn) == 0  # repeated, but sent less than RENOTIFY_S ago
    clock[0] = NOW + timedelta(hours=1)
    assert n.send_pending(conn) == 1 and "×2" in hook.sent[-1]["content"]


def test_error_rate_and_source_rules(env):
    _, conn, _ = env
    for i in range(4):
        record_failure(conn, f"x{i}", TimeoutError("t"), outcome="llm_timeout", channel="web")
    _verifier(conn, env[2]).verify("Czy na Wiśle jest ostrzeżenie?")
    conn.execute("UPDATE sources SET last_error = 'HTTP 503' WHERE source_id = "
                 "(SELECT source_id FROM sources ORDER BY source_id LIMIT 1)")
    failing = {r["source_id"] for r in conn.execute(
        "SELECT source_id FROM sources WHERE enabled AND last_error IS NOT NULL").fetchall()}
    n = Notifier(NotifierConfig(webhook_url=None, error_rate=0.5, min_checks=5), clock=lambda: datetime.now(timezone.utc))
    n.run_once(conn)
    first = {a["fingerprint"]: a["count"] for a in list_alerts(conn, open_only=True)}
    assert "rule:error_rate" in first
    assert {f.split(":")[1] for f in first if f.startswith("source:")} == failing
    for a in list_alerts(conn, open_only=True):
        if a["fingerprint"].startswith("source:"):
            from rag.alerts import acknowledge
            acknowledge(conn, a["alert_id"])
    n.run_once(conn)  # an acknowledged source problem is not raised again while it stays the same
    assert not any(a["fingerprint"].startswith("source:") for a in list_alerts(conn, open_only=True))
