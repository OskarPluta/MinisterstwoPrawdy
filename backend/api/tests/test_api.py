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
from rag.llm import Claim, ClaimExtraction, ClaimVerdict, PassageLabel
from rag.verify import MAX_INPUT_CHARS, MSG_OUTAGE

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

    model = "fake"

    def __init__(self, claim_text: str = "Na Wiśle w Sandomierzu obowiązuje ostrzeżenie hydrologiczne"):
        self.claim_text = claim_text

    def extract_claims(self, text: str, now: datetime) -> ClaimExtraction:
        return ClaimExtraction(
            status="ok", clarification_question=None, claims=[_claim(self.claim_text)], injection_detected=False,
        )

    def verify_claim(self, claim: Claim, evidence_xml: str, now: datetime) -> ClaimVerdict:
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


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_verify_returns_public_result(client):
    r = client.post("/api/verify", json={"text": "Czy na Wiśle w Sandomierzu jest ostrzeżenie?",
                                         "now": "2026-11-21T12:00:00"})
    assert r.status_code == 200
    body: dict[str, Any] = r.json()
    assert body["status"] == "ok"
    assert "error" not in body
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
    assert set(cit) >= {"n", "role", "title", "publisher", "url", "date_label", "text", "is_verbatim"}


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
