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

    def extract_claims(self, text: str, now: datetime) -> ClaimExtraction:
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
