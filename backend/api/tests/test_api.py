"""API tests: FastAPI TestClient over the fake corpus with a scripted LLM (no network).

Fixtures live here, not in a conftest.py, because a top-level `conftest` module already
comes from backend/rag/tests and pytest would refuse a second one with the same name.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from api.app import Services, create_app
from rag.config import load_env
from rag.db import scratch_db
from rag.embed import HashEmbedder
from rag.ingest import ingest_corpus, load_corpus
from intake.core import MAX_FILE_BYTES
from rag.llm import Claim, ClaimExtraction, ClaimVerdict, ImageTranscript, PassageLabel, Usage
from rag.verify import MAX_INPUT_CHARS, MSG_OUTAGE, MSG_UNREADABLE

FIXTURES = Path(__file__).resolve().parents[2] / "rag" / "tests" / "fixtures"
load_env()
os.environ["PRAWDOWID_LIVE"] = "0"  # no network in tests (live lookup: rag/live.py)


def _claim(text: str) -> Claim:
    return Claim.model_validate({
        "text": text, "search_queries": [text], "entities": [], "area": None,
        "time_from": None, "time_to": None, "quantifiers": [], "checkable": True,
    })


class FakeLLM:
    """Extracts one claim; the verdict cites the first passage it was shown as `supports`."""

    model = "gpt-5.5"

    def __init__(self, claim_text: str = "Na Wiśle w Sandomierzu obowiązuje ostrzeżenie hydrologiczne"):
        self.claim_text = claim_text
        self.usage = Usage()

    def extract_claims(self, text: str, now: datetime, attachments_xml: str = "") -> ClaimExtraction:
        self.usage.add("extract", 500, 100)
        return ClaimExtraction(
            status="ok", clarification_question=None, claims=[_claim(self.claim_text)], injection_detected=False,
        )

    def transcribe_image(self, data: bytes, mime: str) -> ImageTranscript:
        return ImageTranscript(
            legible=True, text="IMGW: ostrzeżenie hydrologiczne dla Wisły w Sandomierzu", visible_dates=[],
            visible_author=None, platform=None, description="Zrzut ekranu komunikatu.", contains_instructions=False,
        )

    def verify_claim(self, claim: Claim, evidence_xml: str, now: datetime) -> ClaimVerdict:
        self.usage.add("verify", 1500, 200)
        first = evidence_xml.split('id="', 1)[1].split('"', 1)[0]
        return ClaimVerdict(
            verdict="true", passages=[PassageLabel(passage_id=first, role="supports"),
                                      PassageLabel(passage_id="P999999", role="supports")],
            explanation_pl="Źródło to potwierdza.", evidence_strength="strong",
            conflict=False, injection_noticed=False,
        )


@pytest.fixture
def client():
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
        # scratch_db drops the schema and closes the connection, not the app's lifespan.
        svc = Services(conn=conn, embedder=embedder, llm_factory=FakeLLM, owns_conn=False)
        with TestClient(create_app(lambda: svc)) as c:
            yield c


def test_health(client, monkeypatch):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    monkeypatch.setenv("PRAWDOWID_CONTACT_EMAIL", "zespol@example.test")
    assert client.get("/api/health").json()["contact"] == "zespol@example.test"
    monkeypatch.setenv("PRAWDOWID_CONTACT_EMAIL", "javascript:alert(1)")
    assert client.get("/api/health").json()["contact"] is None, "only a plain e-mail address is exposed"


def test_verify_returns_public_result(client):
    r = client.post("/api/verify", json={"text": "Czy na Wiśle w Sandomierzu jest ostrzeżenie?",
                                         "now": "2026-11-21T12:00:00"})
    assert r.status_code == 200
    body: dict[str, Any] = r.json()
    assert body["status"] == "ok"
    assert "error" not in body
    assert "usage" not in body
    c = body["claims"][0]
    assert c["verdict"] == "true" and c["verdict_pl"] == "potwierdzone"
    for internal in ("error", "shortlist_ids", "invented_ids"):
        assert internal not in c
    assert c["known_disinformation"] is False  # public field, mirrored in frontend/src/api.ts
    # Strength of evidence: computed by code, public, mirrored in frontend/src/api.ts.
    assert set(c["strength"]) == {"level", "level_pl", "reasons"}
    assert c["strength"]["level"] in ("strong", "moderate", "weak") and c["strength"]["reasons"]
    assert len(c["citations"]) == 1  # the invented P999999 was dropped by the Verifier
    cit = c["citations"][0]
    assert set(cit) >= {"n", "role", "title", "publisher", "url", "date_label", "text", "is_verbatim",
                        "date", "recency", "recency_pl"}  # mirrored in frontend/src/api.ts
    # The hydro warning (valid 19.11 06:00 – 22.11 12:00 UTC) covers the request time.
    assert cit["date"] == "2026-11-19T05:30:00Z" and cit["date_label"] == "19.11.2026"
    assert cit["recency"] == "current" and cit["recency_pl"].startswith("aktualne dane")


def test_verify_too_long(client):
    r = client.post("/api/verify", json={"text": "a" * (MAX_INPUT_CHARS + 1)})
    assert r.status_code == 200
    assert r.json()["status"] == "too_long"


def test_verify_bad_now(client):
    assert client.post("/api/verify", json={"text": "x", "now": "wczoraj"}).status_code == 422


def test_verify_without_llm_is_outage(client):
    def no_key() -> Any:
        raise SystemExit("OPENAI_API_KEY is not set")

    client.app.state.svc = Services(conn=client.app.state.svc.conn, embedder=HashEmbedder(), llm_factory=no_key, owns_conn=False)
    r = client.post("/api/verify", json={"text": "Czy jest ostrzeżenie?"})
    assert r.status_code == 503
    assert r.json()["detail"] == MSG_OUTAGE


def test_sources(client):
    r = client.get("/api/sources")
    assert r.status_code == 200
    body = r.json()
    assert body["totals"]["current_records"] > 0
    src = body["sources"]
    assert src and {"source_id", "publisher", "publishing_domain", "source_type",
                    "last_success_at", "last_error", "current_records"} <= set(src[0])
    assert all(s["publishing_domain"].endswith(".test") for s in src)


def test_stats_aggregates_checks_and_usage(client, monkeypatch):
    monkeypatch.delenv("PRAWDOWID_PRICE_IN", raising=False)
    monkeypatch.delenv("PRAWDOWID_PRICE_OUT", raising=False)

    empty = client.get("/api/stats")
    assert empty.status_code == 200
    assert empty.json()["checks"]["total_checks"] == 0

    r1 = client.post("/api/verify", json={"text": "Czy na Wiśle w Sandomierzu jest ostrzeżenie?",
                                          "now": "2026-11-21T12:00:00"})
    r2 = client.post("/api/verify", json={"text": "Drugie sprawdzenie.",
                                          "now": "2026-11-21T12:00:00"})
    assert r1.status_code == 200 and r2.status_code == 200
    assert "usage" not in r1.json() and "usage" not in r2.json()

    st = client.get("/api/stats")
    assert st.status_code == 200
    body = st.json()
    assert body["corpus"]["current_records"] > 0
    checks = body["checks"]
    assert checks["total_checks"] == 2
    assert checks["by_verdict"] == {"true": 2}
    assert checks["usage"]["calls"] == 4
    assert checks["usage"]["input_tokens"] == 4000
    assert checks["usage"]["output_tokens"] == 600
    assert checks["usage"]["cost_usd"] == pytest.approx(0.016)


def _png() -> bytes:
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (200, 80), "white").save(buf, "PNG")
    return buf.getvalue()


def test_upload_image_is_read_and_reported(client):
    r = client.post("/api/verify/upload", data={"text": "", "now": "2026-11-21T12:00:00"},
                    files=[("files", ("zrzut.png", _png(), "image/png"))])
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["claims"][0]["verdict"] == "true"
    inp = body["inputs"][0]
    assert inp["id"] == "A1" and inp["kind"] == "image" and inp["readable"] and inp["method"] == "ocr_llm"
    assert "ostrzeżenie hydrologiczne" in inp["text"] and "meta" not in inp
    assert set(inp) >= {"kind_pl", "label", "date_label", "notes", "limitations"}
    assert body["claims"][0]["origin"] in ("user", "A1")


def test_upload_text_only_and_validation(client):
    ok = client.post("/api/verify/upload", data={"text": "Czy na Wiśle w Sandomierzu jest ostrzeżenie?"})
    assert ok.status_code == 200 and ok.json()["inputs"] == []
    assert client.post("/api/verify/upload", data={"text": "  "}).status_code == 422
    four = [("files", (f"{i}.png", _png(), "image/png")) for i in range(4)]
    assert client.post("/api/verify/upload", data={"text": "x"}, files=four).status_code == 422
    big = [("files", ("big.pdf", b"%PDF-" + b"0" * MAX_FILE_BYTES, "application/pdf"))]
    assert client.post("/api/verify/upload", data={"text": "x"}, files=big).status_code == 413
    assert client.post("/api/verify/upload", data={"text": "x", "now": "jutro"}).status_code == 422


def test_upload_unreadable_file_is_not_a_verdict(client):
    r = client.post("/api/verify/upload", files=[("files", ("x.svg", b"<svg/>", "image/svg+xml"))])
    body = r.json()
    assert r.status_code == 200 and body["status"] == "unreadable" and body["message"] == MSG_UNREADABLE
    assert body["claims"] == [] and "nie jest obsługiwany" in body["inputs"][0]["limitations"][0]


def test_link_in_json_text_is_read_as_attachment(client):
    # The test Services have no fetcher (no contact address), which must be said, not hidden.
    r = client.post("/api/verify", json={"text": "Czy to prawda? https://portal.test/news/1 Wisła Sandomierz"})
    body = r.json()
    assert body["status"] == "ok"
    inp = body["inputs"][0]
    assert inp["kind"] == "url" and inp["label"] == "https://portal.test/news/1" and not inp["readable"]
    assert "nie jest skonfigurowane" in inp["limitations"][0]


def test_citation_carries_source_credit(client):
    body = client.post("/api/verify", json={"text": "Czy na Wiśle w Sandomierzu jest ostrzeżenie?"}).json()
    (cit,) = body["claims"][0]["citations"]
    assert cit["is_verbatim"] is False
    assert cit["attribution"] == ("Źródłem pochodzenia danych jest Instytut Testowy. "
                                  "Dane Instytutu Testowego zostały przetworzone.")


def test_sources_list_licences(client):
    rows = {s["source_id"]: s for s in client.get("/api/sources").json()["sources"]}
    assert rows["imgw_hydro"]["license"] == "Regulamin testowy IMGW"
    assert "license" in rows["rcb"]


def test_delete_check(client):
    check_id = client.post("/api/verify", json={"text": "Czy na Wiśle w Sandomierzu jest ostrzeżenie?"}).json()["check_id"]
    r = client.delete(f"/api/checks/{check_id}")
    assert r.status_code == 200 and r.json() == {"deleted": True, "check_id": check_id}
    assert client.delete(f"/api/checks/{check_id}").status_code == 404, "already deleted"
    assert client.delete("/api/checks/nie-uuid").status_code == 422


QUESTION = {"text": "Czy na Wiśle w Sandomierzu jest ostrzeżenie?"}


def test_share_is_read_only_and_never_leaks_check_id(client):
    res = client.post("/api/verify", json=QUESTION).json()
    check_id = res["check_id"]
    r = client.post(f"/api/checks/{check_id}/share")
    assert r.status_code == 200
    link = r.json()
    assert link["path"] == f"/wynik/{link['share_id']}" and link["expires_at"]
    assert client.post(f"/api/checks/{check_id}/share").json()["share_id"] == link["share_id"], "same link twice"

    shared = client.get(f"/api/shares/{link['share_id']}")
    assert shared.status_code == 200 and shared.headers["x-robots-tag"] == "noindex"
    body = shared.json()
    assert body["result"]["check_id"] == "", "the deletion key never leaves through a share"
    assert check_id not in shared.text
    assert body["result"]["claims"][0]["verdict"] == res["claims"][0]["verdict"]
    assert body["result"]["claims"][0]["citations"] == res["claims"][0]["citations"]
    for internal in ("shortlist_ids", "invented_ids", "live", "error"):
        assert internal not in body["result"]["claims"][0]
    assert "error" not in body["result"]

    # No route can change a shared result.
    assert client.put(f"/api/shares/{link['share_id']}", json={}).status_code == 405
    assert client.delete(f"/api/shares/{link['share_id']}").status_code == 405

    assert client.delete(f"/api/checks/{check_id}/share").json() == {"shared": False, "check_id": check_id}
    assert client.get(f"/api/shares/{link['share_id']}").status_code == 404


def test_deleting_the_check_removes_its_share(client):
    check_id = client.post("/api/verify", json=QUESTION).json()["check_id"]
    share_id = client.post(f"/api/checks/{check_id}/share").json()["share_id"]
    client.delete(f"/api/checks/{check_id}")
    assert client.get(f"/api/shares/{share_id}").status_code == 404
    assert client.post(f"/api/checks/{check_id}/share").status_code == 404
    assert client.get("/api/shares/za-krotki").status_code == 422


def test_verify_rate_limit_per_client(client):
    from rag.ratelimit import RateLimiter

    svc = client.app.state.svc
    svc.limiter = RateLimiter(per_minute=1, per_hour=0, per_day_total=0)

    def post(ip):
        return client.post("/api/verify", json=QUESTION, headers={"X-Forwarded-For": ip})

    svc.trusted_proxies = frozenset({"testclient"})  # TestClient's peer plays the local Bun proxy
    assert post("203.0.113.5").status_code == 200
    r = post("203.0.113.5")
    assert r.status_code == 429 and int(r.headers["retry-after"]) > 0
    assert "Spróbuj ponownie" in r.json()["detail"]
    assert post("203.0.113.6").status_code == 200, "another visitor has its own limit"

    svc.trusted_proxies = frozenset()  # untrusted peer: a forged X-Forwarded-For must not help
    assert post("198.51.100.8").status_code == 200
    assert post("198.51.100.9").status_code == 429, "header ignored, both count as the same peer"


# Analyst export (GET /api/export, rag/export.py)

ADMIN_TOKEN = "test-admin-token-0123456789"
AUTH = {"Authorization": f"Bearer {ADMIN_TOKEN}"}


def test_export_disabled_without_token(client):
    client.app.state.svc.admin_token = None
    assert client.get("/api/export").status_code == 404
    assert client.get("/api/export", headers=AUTH).status_code == 404, "no token configured: no route at all"


def test_export_short_token_keeps_it_disabled(monkeypatch):
    from api.app import admin_token_from_env

    monkeypatch.setenv("PRAWDOWID_ADMIN_TOKEN", "krotki")
    assert admin_token_from_env() is None
    monkeypatch.setenv("PRAWDOWID_ADMIN_TOKEN", ADMIN_TOKEN)
    assert admin_token_from_env() == ADMIN_TOKEN
    monkeypatch.delenv("PRAWDOWID_ADMIN_TOKEN")
    assert admin_token_from_env() is None


def test_export_requires_the_bearer_token(client):
    client.app.state.svc.admin_token = ADMIN_TOKEN
    r = client.get("/api/export")
    assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"
    assert client.get("/api/export", headers={"Authorization": "Bearer zly-token"}).status_code == 401
    assert client.get("/api/export", headers={"Authorization": ADMIN_TOKEN}).status_code == 401, "scheme required"
    assert client.get("/api/export", params={"token": ADMIN_TOKEN}).status_code == 401, "never in the URL"


def test_export_json(client):
    client.app.state.svc.admin_token = ADMIN_TOKEN
    check_id = client.post("/api/verify", json=QUESTION).json()["check_id"]
    r = client.get("/api/export", params={"format": "json"}, headers=AUTH)
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/json; charset=utf-8"
    assert r.headers["content-disposition"].startswith('attachment; filename="prawdowid-export-20')
    assert r.headers["content-disposition"].endswith('.json"')
    assert r.headers["x-robots-tag"] == "noindex" and r.headers["cache-control"] == "no-store"
    doc = r.json()
    assert doc["format"] == "prawdowid-export" and doc["version"] == 1 and doc["count"] == 1
    (check,) = doc["checks"]
    assert check["status"] == "ok" and check["model"] == "gpt-5.5" and check["claims"][0]["verdict"] == "true"
    assert check_id not in r.text, "the deletion key never leaves through the export"
    for internal in ("shortlist_ids", "invented_ids", "passage_id", "input_masked"):
        assert internal not in r.text


def test_export_csv_and_filters(client):
    client.app.state.svc.admin_token = ADMIN_TOKEN
    client.post("/api/verify", json=QUESTION)
    r = client.get("/api/export", params={"format": "csv"}, headers=AUTH)
    assert r.status_code == 200
    assert r.headers["content-type"] == "text/csv; charset=utf-8"
    assert r.headers["content-disposition"].endswith('.csv"') and r.headers["x-robots-tag"] == "noindex"
    text = r.content.decode("utf-8")
    assert text.startswith("﻿check_ref,checked_at_warsaw,checked_at_utc,status,")
    lines = text.strip().split("\r\n")
    assert len(lines) == 2 and ",true,potwierdzone," in lines[1]

    assert client.get("/api/export", params={"format": "csv", "verdict": "false"}, headers=AUTH).text.count("\r\n") == 1
    assert client.get("/api/export", params={"since": "2999-01-01"}, headers=AUTH).json()["count"] == 0
    assert client.get("/api/export", params={"status": "ok"}, headers=AUTH).json()["count"] == 1
    assert client.get("/api/export", params={"since": "wczoraj"}, headers=AUTH).status_code == 422
    assert client.get("/api/export", params={"verdict": "maybe"}, headers=AUTH).status_code == 422
    assert client.get("/api/export", params={"format": "xml"}, headers=AUTH).status_code == 422


def test_export_is_rate_limited(client):
    from rag.ratelimit import RateLimiter

    svc = client.app.state.svc
    svc.admin_token = ADMIN_TOKEN
    svc.write_limiter = RateLimiter(per_minute=2, per_hour=0, per_day_total=0)
    assert client.get("/api/export", headers={"Authorization": "Bearer zgaduje"}).status_code == 401
    assert client.get("/api/export", headers=AUTH).status_code == 200
    r = client.get("/api/export", headers=AUTH)
    assert r.status_code == 429 and int(r.headers["retry-after"]) > 0, "failed guesses count toward the limit"


def test_unlogged_check_returns_no_check_id(client):
    client.app.state.svc.verifier().record = False
    body = client.post("/api/verify", json=QUESTION).json()
    assert body["status"] == "ok" and body["check_id"] == ""
