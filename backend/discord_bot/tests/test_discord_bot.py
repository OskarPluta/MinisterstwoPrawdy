"""Tests for the Discord bot formatting, security invariants, and verification service (no network)."""

from __future__ import annotations

import asyncio
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import psycopg
import pytest

from discord_bot.bot import DiscordService, create_bot, extract_query_from_message
from discord_bot.format import (
    COLOR_DANGER,
    COLOR_INFO,
    COLOR_SUCCESS,
    COLOR_WARNING,
    DISCORD_DESC_MAX,
    DISCORD_TITLE_MAX,
    DISCORD_TOTAL_EMBEDS_MAX,
    INJECTION_MSG,
    format_check_result,
    format_sources_embed,
)
from rag.config import load_env
from rag.db import scratch_db
from rag.embed import HashEmbedder
from rag.ingest import ingest_corpus, load_corpus
from rag.llm import Claim, ClaimExtraction, ClaimVerdict, PassageLabel
from rag.timeutil import WARSAW
from rag.verify import MSG_OUTAGE, VERDICT_PL, CheckResult, Citation, ClaimResult

FIXTURES = Path(__file__).resolve().parents[2] / "rag" / "tests" / "fixtures"
load_env()
os.environ["PRAWDOWID_LIVE"] = "0"


def _claim(text: str) -> Claim:
    return Claim.model_validate(
        {
            "text": text,
            "search_queries": [text],
            "entities": [],
            "area": None,
            "time_from": None,
            "time_to": None,
            "quantifiers": [],
            "checkable": True,
        }
    )


class FakeLLM:
    model = "fake"

    def __init__(self, claim_text: str = "Na Wiśle w Sandomierzu obowiązuje ostrzeżenie hydrologiczne"):
        self.claim_text = claim_text

    def extract_claims(self, text: str, now: datetime, attachments_xml: str = "") -> ClaimExtraction:
        return ClaimExtraction(
            status="ok",
            clarification_question=None,
            claims=[_claim(self.claim_text)],
            injection_detected=False,
        )

    def verify_claim(self, claim: Claim, evidence_xml: str, now: datetime) -> ClaimVerdict:
        first = evidence_xml.split('id="', 1)[1].split('"', 1)[0]
        return ClaimVerdict(
            verdict="true",
            passages=[
                PassageLabel(passage_id=first, role="supports"),
                PassageLabel(passage_id="P999999", role="supports"),
            ],
            explanation_pl="Źródło to potwierdza.",
            evidence_strength="strong",
            conflict=False,
            injection_noticed=False,
        )


def _embed_len(embed) -> int:
    total = len(embed.title or "") + len(embed.description or "")
    if embed.footer and embed.footer.text:
        total += len(embed.footer.text)
    return total


def test_verdict_colors_and_insufficient_never_red():
    expected = {
        "true": COLOR_SUCCESS,
        "false": COLOR_DANGER,
        "misleading": COLOR_WARNING,
        "insufficient": COLOR_INFO,
        "unverifiable": COLOR_INFO,
        "error": COLOR_WARNING,
    }
    for verdict, color in expected.items():
        res = CheckResult(
            check_id="c1",
            status="ok",
            message=None,
            claims=[
                ClaimResult(
                    claim="Przykładowe twierdzenie",
                    verdict=verdict,
                    verdict_pl=VERDICT_PL[verdict],
                    explanation="Wyjaśnienie testowe.",
                )
            ],
        )
        out = format_check_result(res)
        assert len(out.embeds) == 1
        assert out.embeds[0].color is not None
        assert out.embeds[0].color.value == color
        if verdict in ("insufficient", "unverifiable"):
            assert out.embeds[0].color.value != COLOR_DANGER


def test_opinion_with_known_disinformation_uses_warning():
    res = CheckResult(
        check_id="c2",
        status="ok",
        message=None,
        claims=[
            ClaimResult(
                claim="Opinia z narracją",
                verdict="unverifiable",
                verdict_pl=VERDICT_PL["unverifiable"],
                explanation="To ocena.",
                known_disinformation=True,
            )
        ],
    )
    out = format_check_result(res)
    assert out.embeds[0].color.value == COLOR_WARNING
    assert "Ta opinia powtarza znaną narrację dezinformacyjną" in (out.embeds[0].title or "")
    assert "Znana narracja dezinformacyjna" in (out.embeds[0].description or "")


def test_non_ok_statuses():
    for status, expected_color in [
        ("needs_clarification", COLOR_WARNING),
        ("too_long", COLOR_WARNING),
        ("out_of_scope", COLOR_INFO),
        ("error", COLOR_DANGER),
    ]:
        res = CheckResult(check_id="c3", status=status, message="Komunikat z https://evil.test/x")
        out = format_check_result(res)
        assert len(out.embeds) == 1
        assert out.embeds[0].color.value == expected_color
        desc = out.embeds[0].description or ""
        assert "https://evil.test" not in desc
        assert "(adres usunięty)" in desc


def test_model_urls_stripped_and_markdown_escaped_and_citation_types():
    res = CheckResult(
        check_id="c4",
        status="ok",
        message="Uwaga z linkiem https://fake.test/msg",
        injection_detected=True,
        claims=[
            ClaimResult(
                claim="Twierdzenie z [linkiem](https://evil.test) i <tag>",
                verdict="misleading",
                verdict_pl=VERDICT_PL["misleading"],
                explanation="Zobacz https://phishing.test oraz [klik](http://bad.test).",
                conflict=True,
                notes=["Ostrzeżenie ze źródła IMGW wygasło."],
                citations=[
                    Citation(
                        n=1,
                        passage_id="P1",
                        role="supports",
                        title="Komunikat [oficjalny]",
                        publisher="MSZ",
                        url="https://gov.test/komunikat",
                        date_label="2026-11-20",
                        text="Cytat dosłowny z komunikatu.",
                        is_verbatim=True,
                    ),
                    Citation(
                        n=2,
                        passage_id="P2",
                        role="context",
                        title="Ostrzeżenie hydro",
                        publisher="IMGW-PIB",
                        url="https://imgw.test/hydro#1",
                        date_label="2026-11-21",
                        text="Zjawisko: wezbranie. Stopień: 2.",
                        is_verbatim=False,
                    ),
                    Citation(
                        n=3,
                        passage_id="P3",
                        role="context",
                        title="Zły protokół",
                        publisher="Test",
                        url="javascript:alert(1)",
                        date_label="2026-11-21",
                        text="Tekst bez linku.",
                        is_verbatim=True,
                    ),
                ],
            )
        ],
    )
    out = format_check_result(res)
    assert out.content is not None
    assert INJECTION_MSG in out.content
    assert "https://fake.test" not in out.content
    assert "(adres usunięty)" in out.content

    desc = out.embeds[0].description or ""
    assert "evil.test" not in desc
    assert "phishing.test" not in desc
    assert "bad.test" not in desc
    assert "javascript:" not in desc
    assert "(adres usunięty)" in desc
    assert "[Komunikat \\[oficjalny\\]](https://gov.test/komunikat)" in desc
    assert "> Cytat dosłowny z komunikatu." in desc
    assert "Dane ze źródła: Zjawisko: wezbranie. Stopień: 2." in desc
    assert "Źródła podają różne informacje" in desc
    assert "⚠ Ostrzeżenie ze źródła IMGW wygasło." in desc


def test_long_multi_claim_respects_discord_limits():
    long_text = "Bardzo długie zdanie z faktami i szczegółami. " * 120
    claims = [
        ClaimResult(
            claim=f"Twierdzenie {i}: " + ("abc " * 40),
            verdict="true",
            verdict_pl=VERDICT_PL["true"],
            explanation=long_text,
            citations=[
                Citation(
                    n=j,
                    passage_id=f"P{j}",
                    role="supports",
                    title=f"Źródło {j}",
                    publisher="MSZ",
                    url=f"https://gov.test/doc/{j}",
                    date_label="2026-11-21",
                    text=long_text,
                    is_verbatim=True,
                )
                for j in range(1, 5)
            ],
        )
        for i in range(5)
    ]
    res = CheckResult(check_id="long", status="ok", message=None, claims=claims)
    out = format_check_result(res)
    assert len(out.embeds) == 5
    for embed in out.embeds:
        assert len(embed.title or "") <= DISCORD_TITLE_MAX
        assert len(embed.description or "") <= DISCORD_DESC_MAX
    assert sum(_embed_len(e) for e in out.embeds) <= DISCORD_TOTAL_EMBEDS_MAX


def test_format_check_result_accepts_api_dict_payload():
    payload = {
        "check_id": "abc",
        "status": "ok",
        "message": None,
        "injection_detected": False,
        "duration_ms": 42,
        "claims": [
            {
                "claim": "W Sandomierzu obowiązuje ostrzeżenie.",
                "verdict": "true",
                "verdict_pl": "potwierdzone",
                "explanation": "IMGW wydał ostrzeżenie.",
                "notes": [],
                "conflict": False,
                "known_disinformation": False,
                "strength": {
                    "level": "moderate",
                    "level_pl": "umiarkowana",
                    "reasons": ["1 niezależne źródło", "źródło pierwotne (urząd)"],
                },
                "citations": [
                    {
                        "n": 1,
                        "role": "supports",
                        "title": "Ostrzeżenie hydrologiczne",
                        "publisher": "IMGW-PIB",
                        "url": "https://imgw.test/1",
                        "date_label": "2026-11-21",
                        "text": "Stopień 2.",
                        "is_verbatim": False,
                        "attribution": "Źródłem pochodzenia danych jest IMGW-PIB. Dane zostały przetworzone.",
                    }
                ],
            }
        ],
    }
    out = format_check_result(payload)
    assert len(out.embeds) == 1
    desc = out.embeds[0].description or ""
    assert "[Ostrzeżenie hydrologiczne](https://imgw.test/1)" in desc
    assert "Dane ze źródła: Stopień 2." in desc
    assert "_Źródłem pochodzenia danych jest IMGW-PIB. Dane zostały przetworzone._" in desc
    assert "**Siła dowodów:** umiarkowana" in desc
    assert "• 1 niezależne źródło" in desc
    assert out.embeds[0].color.value == COLOR_SUCCESS  # strength never changes the verdict colour


def test_format_sources_embed():
    embed = format_sources_embed(
        {"current_records": 12},
        [
            {
                "source_id": "imgw-hydro",
                "publisher": "IMGW-PIB",
                "publishing_domain": "imgw.test",
                "source_type": "structured",
                "current_records": 7,
            },
            {
                "source_id": "msz",
                "publisher": "MSZ",
                "publishing_domain": "gov.test",
                "source_type": "official",
                "current_records": 5,
            },
        ],
    )
    assert embed.color.value == COLOR_INFO
    desc = embed.description or ""
    assert "**Źródła w korpusie:** 2" in desc
    assert "**Domeny:** 2" in desc
    assert "IMGW-PIB" in desc


def test_extract_query_from_message():
    assert extract_query_from_message("Cześć wszystkim", bot_user_id=999, is_dm=False) is None
    assert extract_query_from_message("<@999>", bot_user_id=999, is_dm=False) == ""
    assert extract_query_from_message("<@!999>  Czy to prawda? ", bot_user_id=999, is_dm=False) == "Czy to prawda?"
    assert extract_query_from_message("Czy to prawda?", bot_user_id=999, is_dm=True) == "Czy to prawda?"
    assert extract_query_from_message("<@999> Pytanie na DM", bot_user_id=999, is_dm=True) == "Pytanie na DM"


def test_discord_service_without_llm_is_outage():
    def no_key() -> Any:
        raise SystemExit("OPENAI_API_KEY is not set")

    svc = DiscordService(conn=object(), embedder=HashEmbedder(), llm_factory=no_key, owns_conn=False)
    res = asyncio.run(svc.verify("Czy jest ostrzeżenie?"))
    assert res.status == "error"
    assert res.message == MSG_OUTAGE


def test_discord_service_limits_each_user():
    from rag.ratelimit import RateLimiter

    def no_key() -> Any:
        raise SystemExit("OPENAI_API_KEY is not set")

    svc = DiscordService(conn=object(), embedder=HashEmbedder(), llm_factory=no_key, owns_conn=False,
                         limiter=RateLimiter(per_minute=1, per_hour=0, per_day_total=0))
    assert asyncio.run(svc.verify("Czy jest ostrzeżenie?", user="1")).message == MSG_OUTAGE
    limited = asyncio.run(svc.verify("Czy jest ostrzeżenie?", user="1"))
    assert limited.status == "error" and "zbyt wiele" in (limited.message or "")
    assert asyncio.run(svc.verify("Czy jest ostrzeżenie?", user="2")).message == MSG_OUTAGE, "per user"


def test_discord_service_api_outage_returns_error():
    svc = DiscordService(api_url="http://127.0.0.1:1")
    res = asyncio.run(svc.verify("Czy jest ostrzeżenie?"))
    assert res.status == "error"
    assert res.message == MSG_OUTAGE


def test_discord_service_end_to_end_with_db():
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
        svc = DiscordService(conn=conn, embedder=embedder, llm_factory=FakeLLM, owns_conn=False)
        now = datetime(2026, 11, 21, 12, 0, tzinfo=WARSAW)
        res = asyncio.run(
            svc.verify(
                "Czy na Wiśle w Sandomierzu jest ostrzeżenie? Kontakt: jan@example.pl",
                now=now,
            )
        )
        assert res.status == "ok"
        assert len(res.claims) == 1
        claim = res.claims[0]
        assert claim.verdict == "true"
        assert claim.verdict_pl == "potwierdzone"
        assert len(claim.citations) == 1  # invented P999999 was dropped by Verifier

        formatted = format_check_result(res)
        assert len(formatted.embeds) == 1
        assert formatted.embeds[0].color.value == COLOR_SUCCESS

        row = conn.execute("SELECT input_masked FROM checks WHERE check_id = %s", (res.check_id,)).fetchone()
        assert row is not None
        assert "jan@example.pl" not in row["input_masked"]
        assert "[email]" in row["input_masked"]

        totals, sources = asyncio.run(svc.sources())
        assert totals["current_records"] > 0
        assert all(s["publishing_domain"].endswith(".test") for s in sources)


def test_create_bot_registers_slash_commands():
    svc = DiscordService(api_url="http://127.0.0.1:8000")
    bot = create_bot(svc, guild_id=123456)
    cmd_names = {cmd.name for cmd in bot.tree.get_commands()}
    assert {"sprawdz", "zrodla"} <= cmd_names


# --- share link and deletion of the check log ---------------------------------------------

from types import SimpleNamespace  # noqa: E402

import httpx  # noqa: E402

from discord_bot.bot import ActionOutcome  # noqa: E402
from discord_bot.format import (  # noqa: E402
    MSG_ALREADY_DELETED,
    MSG_BAD_ID,
    MSG_DELETED,
    MSG_NOT_FOUND_DELETE,
    MSG_NOT_FOUND_SHARE,
    MSG_NOT_REQUESTER,
    MSG_SHARE_OFF,
    MSG_UNSHARED,
    format_private_note,
    format_share_link,
    parse_check_id,
    public_base_url,
    share_url,
)
from discord_bot.views import CheckHandle, OpenerView, PanelView, check_handle, is_requester  # noqa: E402
from rag.ratelimit import RateLimiter  # noqa: E402

CHECK_ID = "3f2b8c1e-9a4d-4e6f-8b2a-1c3d5e7f9a0b"
SHARE_ID = "AbCdEfGh_-123456"
BASE = "https://prawdowid.test"


@pytest.fixture
def db_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("no TEST_DATABASE_URL / DATABASE_URL set (see .env.example)")
    try:
        psycopg.connect(url, connect_timeout=3).close()
    except psycopg.OperationalError as e:
        pytest.skip(f"PostgreSQL not reachable: {e}".splitlines()[0])
    return url


def _ok_result(check_id: str = CHECK_ID) -> CheckResult:
    return CheckResult(
        check_id=check_id,
        status="ok",
        message=None,
        claims=[ClaimResult(claim="Twierdzenie", verdict="true", verdict_pl=VERDICT_PL["true"],
                            explanation="Wyjaśnienie.")],
    )


def test_parse_check_id_and_public_url():
    assert parse_check_id(f"  `{CHECK_ID.upper()}` ") == CHECK_ID
    assert parse_check_id("abc") is None
    assert parse_check_id("") is None
    assert parse_check_id(CHECK_ID + "/share") is None

    assert public_base_url("https://prawdowid.test/") == BASE
    assert public_base_url("http://localhost:3000") == "http://localhost:3000"
    assert public_base_url("") is None
    assert public_base_url(None) is None
    assert public_base_url("javascript:alert(1)") is None
    assert public_base_url("prawdowid.test") is None

    assert share_url(BASE, f"/wynik/{SHARE_ID}") == f"{BASE}/wynik/{SHARE_ID}"
    assert share_url(None, f"/wynik/{SHARE_ID}") is None
    assert share_url(BASE, "//evil.test/wynik/x") is None
    assert share_url(BASE, f"/wynik/{SHARE_ID}?x=1") is None


def test_format_share_link_date_in_warsaw():
    url = f"{BASE}/wynik/{SHARE_ID}"
    # 23:30 UTC on 21 Dec is already 22 Dec in Warsaw.
    assert format_share_link(url, "2026-12-21T23:30:00Z") == (
        f"Link do wyniku (tylko do odczytu, działa do 22.12.2026): {url}"
    )
    assert format_share_link(url, None) == f"Link do wyniku (tylko do odczytu): {url}"


def test_private_note_says_plainly_when_sharing_is_off():
    on = format_private_note(CHECK_ID, shareable=True, sharing_enabled=True)
    assert CHECK_ID in on and f"/usun identyfikator:{CHECK_ID}" in on and "/udostepnij" in on
    off = format_private_note(CHECK_ID, shareable=True, sharing_enabled=False)
    assert MSG_SHARE_OFF in off and "/udostepnij" not in off
    error = format_private_note(CHECK_ID, shareable=False, sharing_enabled=True)
    assert "/udostepnij" not in error and MSG_SHARE_OFF not in error


def test_write_rejects_bad_id_and_share_without_public_url():
    svc = DiscordService(conn=None, owns_conn=False, public_url=None)
    assert svc.sharing_enabled is False
    assert svc.delete_sync("nie-uuid", user="1") == ActionOutcome(False, MSG_BAD_ID)
    # No public URL: no share is created at all (there would be no working link to give).
    assert svc.share_sync(CHECK_ID, user="1") == ActionOutcome(False, MSG_SHARE_OFF)


def test_write_is_rate_limited_per_user():
    svc = DiscordService(conn=None, owns_conn=False, public_url=BASE,
                         write_limiter=RateLimiter(per_minute=2, per_hour=0, per_day_total=0))
    # conn=None makes the write an outage, but it still counts against the limit.
    assert svc.delete_sync(CHECK_ID, user="1").message == MSG_OUTAGE
    assert svc.unshare_sync(CHECK_ID, user="1").message == MSG_OUTAGE
    limited = svc.share_sync(CHECK_ID, user="1")
    assert not limited.ok and "zbyt wiele" in limited.message
    assert svc.delete_sync(CHECK_ID, user="2").message == MSG_OUTAGE, "per user"


def test_share_unshare_delete_direct_mode(db_url, monkeypatch):
    monkeypatch.setenv("PRAWDOWID_CHECKS_RETENTION_DAYS", "30")
    with scratch_db(db_url) as conn:
        embedder = HashEmbedder()
        ingest_corpus(conn, load_corpus(FIXTURES / "fake_corpus.json"), embedder)
        svc = DiscordService(conn=conn, embedder=embedder, llm_factory=FakeLLM, owns_conn=False,
                             public_url=BASE + "/")
        now = datetime(2026, 11, 21, 12, 0, tzinfo=WARSAW)
        res = asyncio.run(svc.verify("Czy na Wiśle w Sandomierzu jest ostrzeżenie?", now=now, user="42"))
        assert res.status == "ok" and parse_check_id(res.check_id) == res.check_id

        shared = asyncio.run(svc.share(res.check_id, user="42"))
        assert shared.ok and shared.url and shared.url.startswith(f"{BASE}/wynik/")
        assert shared.message.startswith("Link do wyniku (tylko do odczytu, działa do ")
        assert shared.message.endswith(shared.url)
        assert res.check_id not in shared.message  # the public link never carries the deletion key
        row = conn.execute("SELECT share_id FROM checks WHERE check_id = %s", (res.check_id,)).fetchone()
        assert shared.url.endswith("/wynik/" + row["share_id"])
        assert asyncio.run(svc.share(res.check_id, user="42")).url == shared.url, "same link twice"

        assert asyncio.run(svc.unshare(res.check_id, user="42")) == ActionOutcome(True, MSG_UNSHARED)
        row = conn.execute("SELECT share_id FROM checks WHERE check_id = %s", (res.check_id,)).fetchone()
        assert row["share_id"] is None
        assert asyncio.run(svc.share(res.check_id, user="42")).ok

        assert asyncio.run(svc.delete(res.check_id, user="42")) == ActionOutcome(True, MSG_DELETED)
        assert conn.execute("SELECT 1 FROM checks WHERE check_id = %s", (res.check_id,)).fetchone() is None
        assert asyncio.run(svc.delete(res.check_id, user="42")) == ActionOutcome(False, MSG_NOT_FOUND_DELETE)
        assert asyncio.run(svc.share(res.check_id, user="42")) == ActionOutcome(False, MSG_NOT_FOUND_SHARE)


def test_unlogged_check_has_no_id_direct_mode(db_url, monkeypatch):
    monkeypatch.setenv("PRAWDOWID_CHECKS_RETENTION_DAYS", "0")  # no check log at all
    with scratch_db(db_url) as conn:
        embedder = HashEmbedder()
        ingest_corpus(conn, load_corpus(FIXTURES / "fake_corpus.json"), embedder)
        svc = DiscordService(conn=conn, embedder=embedder, llm_factory=FakeLLM, owns_conn=False, public_url=BASE)
        now = datetime(2026, 11, 21, 12, 0, tzinfo=WARSAW)
        res = asyncio.run(svc.verify("Czy na Wiśle w Sandomierzu jest ostrzeżenie?", now=now, user="42"))
        assert res.status == "ok" and res.check_id == ""
        assert check_handle(res, 42, svc) is None  # nothing to share or delete: no buttons


def _api_service(handler, **kw) -> tuple[DiscordService, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    kw.setdefault("public_url", BASE)
    svc = DiscordService(api_url="http://api.test/", http_transport=httpx.MockTransport(record), **kw)
    return svc, seen


def test_share_and_delete_via_api():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == f"/api/checks/{CHECK_ID}/share":
            return httpx.Response(200, json={"share_id": SHARE_ID, "path": f"/wynik/{SHARE_ID}",
                                             "expires_at": "2026-12-22T10:00:00Z"})
        if request.method == "DELETE" and request.url.path in (f"/api/checks/{CHECK_ID}",
                                                                f"/api/checks/{CHECK_ID}/share"):
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(500)

    svc, seen = _api_service(handler)
    out = svc.share_sync(CHECK_ID, user="77")
    assert out == ActionOutcome(
        True, f"Link do wyniku (tylko do odczytu, działa do 22.12.2026): {BASE}/wynik/{SHARE_ID}",
        url=f"{BASE}/wynik/{SHARE_ID}",
    )
    assert svc.unshare_sync(CHECK_ID, user="77") == ActionOutcome(True, MSG_UNSHARED)
    assert svc.delete_sync(CHECK_ID, user="77") == ActionOutcome(True, MSG_DELETED)
    assert [(r.method, r.url.path) for r in seen] == [
        ("POST", f"/api/checks/{CHECK_ID}/share"),
        ("DELETE", f"/api/checks/{CHECK_ID}/share"),
        ("DELETE", f"/api/checks/{CHECK_ID}"),
    ]
    assert all(r.headers["x-forwarded-for"] == "discord:77" for r in seen)


def test_api_errors_surface_detail():
    responses = {
        "404": httpx.Response(404, json={"detail": "Nie ma zapisu o tym identyfikatorze (mógł już zostać usunięty)."}),
        "429": httpx.Response(429, json={"detail": "Wysłano zbyt wiele próśb. Spróbuj ponownie za minutę."}),
        "500": httpx.Response(500, text="boom"),
        "badpath": httpx.Response(200, json={"share_id": "x", "path": "https://evil.test/", "expires_at": None}),
    }
    for key, resp in responses.items():
        svc, _ = _api_service(lambda request, resp=resp: resp)
        out = svc.share_sync(CHECK_ID, user="1") if key == "badpath" else svc.delete_sync(CHECK_ID, user="1")
        assert not out.ok and out.url is None
        if key in ("404", "429"):
            assert out.message == resp.json()["detail"]
        else:
            assert out.message == MSG_OUTAGE

    svc, seen = _api_service(lambda request: httpx.Response(200, json={}), public_url=None)
    assert svc.share_sync(CHECK_ID, user="1").message == MSG_SHARE_OFF
    assert seen == [], "no share is created when there is no public URL"


def test_check_handle_only_for_logged_checks():
    svc_on = DiscordService(api_url="http://api.test", public_url=BASE)
    svc_off = DiscordService(api_url="http://api.test")
    assert check_handle(CheckResult(check_id="", status="error", message="x"), 1, svc_on) is None
    assert check_handle(_ok_result(), 1, svc_on).shareable is True
    assert check_handle(_ok_result(), 1, svc_off).shareable is False
    err = CheckResult(check_id=CHECK_ID, status="error", message=MSG_OUTAGE)
    handle = check_handle(err, 1, svc_on)
    assert handle is not None and handle.shareable is False  # an error page is not worth a link, delete stays


class _FakeResponse:
    def __init__(self) -> None:
        self.sent: list[tuple[Any, dict]] = []
        self.edits: list[dict] = []
        self.deferred = False

    async def send_message(self, content=None, **kw) -> None:
        self.sent.append((content, kw))

    async def edit_message(self, **kw) -> None:
        self.edits.append(kw)

    async def defer(self, **kw) -> None:
        self.deferred = True


class _FakeInteraction:
    def __init__(self, user_id: int) -> None:
        self.user = SimpleNamespace(id=user_id)
        self.response = _FakeResponse()
        self.edits: list[dict] = []

    async def edit_original_response(self, **kw) -> None:
        self.edits.append(kw)

    async def original_response(self):
        return None


class _FakeService:
    sharing_enabled = True

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    async def share(self, check_id, user):
        self.calls.append(("share", check_id, user))
        return ActionOutcome(True, f"Link do wyniku (tylko do odczytu): {BASE}/wynik/{SHARE_ID}",
                             url=f"{BASE}/wynik/{SHARE_ID}")

    async def unshare(self, check_id, user):
        self.calls.append(("unshare", check_id, user))
        return ActionOutcome(True, MSG_UNSHARED)

    async def delete(self, check_id, user):
        self.calls.append(("delete", check_id, user))
        return ActionOutcome(True, MSG_DELETED)


def _labels(view) -> list[str]:
    return [item.label for item in view.children]


def test_buttons_answer_only_the_requester():
    assert is_requester(10, 10) and not is_requester(11, 10) and not is_requester(None, 10)
    handle = CheckHandle(check_id=CHECK_ID, requester_id=10, shareable=True, sharing_enabled=True)
    for view in (PanelView(_FakeService(), handle), OpenerView(_FakeService(), handle)):
        other = _FakeInteraction(11)
        assert asyncio.run(view.interaction_check(other)) is False
        assert other.response.sent == [(MSG_NOT_REQUESTER, {"ephemeral": True})]
        owner = _FakeInteraction(10)
        assert asyncio.run(view.interaction_check(owner)) is True
        assert owner.response.sent == []


def test_check_id_never_in_public_message():
    svc = DiscordService(api_url="http://api.test", public_url=BASE)
    res = _ok_result()
    formatted = format_check_result(res)
    public = repr(formatted.content) + repr([e.to_dict() for e in formatted.embeds])
    assert CHECK_ID not in public
    opener = OpenerView(svc, check_handle(res, 10, svc))
    assert _labels(opener) == ["Udostępnij lub usuń"]
    # Components of a public message reach every member's client: no check_id in them either.
    assert CHECK_ID not in repr(opener.to_components())
    # The private panel shows the id in its text only (ephemeral / DM), never in a custom_id.
    panel = PanelView(svc, check_handle(res, 10, svc))
    assert CHECK_ID in panel.text()
    assert CHECK_ID not in repr(panel.to_components())


def test_panel_share_unshare_and_delete_flow():
    service = _FakeService()
    handle = CheckHandle(check_id=CHECK_ID, requester_id=10, shareable=True, sharing_enabled=True)
    opener = OpenerView(service, handle)
    edited: list[dict] = []

    class _Msg:
        async def edit(self, **kw):
            edited.append(kw)

    opener.message = _Msg()

    # The public button opens the private panel as an ephemeral message.
    click = _FakeInteraction(10)
    asyncio.run(opener.on_open(click))
    (content, kw), = click.response.sent
    assert kw["ephemeral"] is True and CHECK_ID in content
    panel = kw["view"]
    assert _labels(panel) == ["Utwórz link do wyniku", "Usuń zapis"]

    asyncio.run(panel.on_share(_FakeInteraction(10)))
    assert f"{BASE}/wynik/{SHARE_ID}" in panel.text()
    assert _labels(panel) == ["Wyłącz link", "Usuń zapis"]

    asyncio.run(panel.on_unshare(_FakeInteraction(10)))
    assert f"{BASE}/wynik/{SHARE_ID}" not in panel.text() and MSG_UNSHARED in panel.text()
    assert _labels(panel) == ["Utwórz link do wyniku", "Usuń zapis"]

    asyncio.run(panel.on_delete(_FakeInteraction(10)))
    assert _labels(panel) == ["Tak, usuń zapis", "Anuluj"]
    asyncio.run(panel.on_cancel(_FakeInteraction(10)))
    assert _labels(panel) == ["Utwórz link do wyniku", "Usuń zapis"]
    asyncio.run(panel.on_delete(_FakeInteraction(10)))
    done = _FakeInteraction(10)
    asyncio.run(panel.on_delete_confirmed(done))
    assert done.edits == [{"content": MSG_DELETED, "view": None}]
    assert handle.deleted and panel.is_finished()
    assert edited == [{"view": None}], "the public button is removed after deletion"
    assert [c[0] for c in service.calls] == ["share", "unshare", "delete"]
    assert all(c[1:] == (CHECK_ID, "10") for c in service.calls)

    late = _FakeInteraction(10)
    asyncio.run(opener.on_open(late))
    assert late.response.sent == [(MSG_ALREADY_DELETED, {"ephemeral": True})]


def test_create_bot_registers_share_and_delete_commands():
    bot = create_bot(DiscordService(api_url="http://127.0.0.1:8000"), guild_id=123456)
    cmds = {cmd.name: cmd for cmd in bot.tree.get_commands()}
    assert {"usun", "udostepnij"} <= set(cmds)
    assert [p.name for p in cmds["usun"].parameters] == ["identyfikator"]
    assert [p.name for p in cmds["udostepnij"].parameters] == ["identyfikator", "wylacz"]
