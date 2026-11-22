"""Adapter mcp_tool na zapisanych prawdziwych odpowiedziach EUvsDisinfo (fixtures/*.sse, 2026-11-22)."""
from __future__ import annotations

from pathlib import Path

from crawler.fetcher import Resp
from crawler.mcp import parse_date, parse_records, tool_text

FIX = Path(__file__).parent / "fixtures"


def load(name: str) -> str:
    raw = (FIX / name).read_text(encoding="utf-8")
    return tool_text(Resp(url="u", final_url="u", status=200, headers={}, text=raw))


def test_cases_real_response():
    recs, has_next = parse_records(load("euvsdisinfo_cases.sse"))
    assert has_next is True and len(recs) == 2
    r = recs[0]
    assert r["title"] == "European countries are facing insolvency because of their support of Ukraine"
    assert r["fields"]["ID"] == "34847d2e-4b1f-4f52-a290-429ab777a949"
    assert r["fields"]["Source URL"].startswith("https://euvsdisinfo.eu/report/european-countries")
    assert r["fields"]["Claim"].startswith("The US-Russia war in Ukraine")
    assert "Fact Check: This claim is made" in r["body"] and "**" not in r["body"]
    assert "sputnik.by" in r["fields"]["Disinformation Links"], "linki do dezinformacji w treści"
    assert "Total cases" not in recs[1]["body"], "stopka z liczbą rekordów nie trafia do ostatniego rekordu"
    assert parse_date(r["fields"]["Date"]) == "2026-11-16T00:00:00+00:00"


def test_articles_real_response():
    recs, has_next = parse_records(load("euvsdisinfo_articles.sse"))
    assert has_next is True and len(recs) == 1
    r = recs[0]
    assert r["title"] == "‘Russophobia’: a narrative for every occasion"
    assert r["fields"]["Source URL"] == "https://euvsdisinfo.eu/russophobia-a-narrative-for-every-occasion/"
    assert "Estonian Foreign Minister" in r["fields"]["Content"]
    assert parse_date(r["fields"]["Date"]) == "2026-11-20T10:08:54+00:00"
