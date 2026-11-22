"""Adapter mcp_tool (EUvsDisinfo) bez sieci: sztuczny Fetcher z odpowiedziami SSE."""
from __future__ import annotations

import json

import pytest

from crawler.adapters import Ctx, Stats
from crawler.fetcher import HttpError, Resp
from crawler.mcp import parse_records, parse_sse_json, run_mcp_tool
from crawler.storage import Store

CASES = [
    {"id": str(i), "title": f"Sprawa {i}", "claim": f"Twierdzenie {i}", "fact": f"Fakt {i}",
     "date": f"2026-11-{20 - i:02d}", "url": f"https://euvsdisinfo.eu/report/sprawa-{i}/"}
    for i in range(1, 6)
]


def md(cases: list[dict], offset: int) -> str:
    out = [f"Found {len(cases)} cases:\n"]
    for n, c in enumerate(cases, offset + 1):
        out.append(f"**Case {n}: {c['title']}**\n- **ID:** {c['id']}\n- **Date:** {c['date']}\n"
                   f"- **Claim:** {c['claim']}\n  ciąg dalszy\n- **Fact Check:** {c['fact']}\n"
                   f"- **Keywords:** Ukraina, NATO\n- **URL:** {c['url']}\n\n---\n")
    return "\n".join(out)


def sse(obj: dict) -> str:
    return f"event: message\ndata: {json.dumps(obj)}\n\n"


class FakeMcp:
    """Pierwsze tools/call bez sesji → 400; potem initialize i właściwe wyniki."""

    def __init__(self, cases=CASES):
        self.cases, self.calls, self.session = cases, [], None

    def http_send(self, method, url, json_body=None, accept=None, headers=None, private=None):
        self.calls.append((json_body, dict(headers or {}), private))
        assert "text/event-stream" in accept and method == "POST"
        if private != {"Authorization": "Bearer SEKRET"}:
            raise HttpError("HTTP 401", 401)
        m = json_body["method"]
        if m == "initialize":
            self.session = "S-1"
            return Resp(url=url, final_url=url, status=200, headers={"Mcp-Session-Id": "S-1"},
                        text=sse({"jsonrpc": "2.0", "id": json_body["id"], "result": {}}))
        if m == "notifications/initialized":
            return Resp(url=url, final_url=url, status=202, headers={}, text="")
        if headers.get("mcp-session-id") != self.session:
            raise HttpError("HTTP 400 bad session", 400)
        a = json_body["params"]["arguments"]
        page = self.cases[a["offset"]:a["offset"] + a["limit"]]
        text = md(page, a["offset"]) if page else "No cases found."
        return Resp(url=url, final_url=url, status=200, headers={},
                    text=sse({"jsonrpc": "2.0", "id": json_body["id"],
                              "result": {"content": [{"type": "text", "text": text}]}}))

    def commit_validators(self, resp):
        pass


SRC = {"name": "eu", "institution": "EUvsDisinfo", "doc_type": "weryfikacja", "type": "mcp_tool",
       "urls": ["https://mcp.invalid/mcp"], "headers": {"Authorization": "Bearer ${TEST_EUVSD_KEY}"},
       "tool": "cases", "page_size": 2, "max_pages": 3, "backfill_pages_per_run": 1,
       "url_pattern": "euvsdisinfo\\.eu/report/", "date_fields": ["date"]}


def docs(d):
    return [json.loads(l) for l in (d / "documents.jsonl").read_text().splitlines()]


def test_parse_records_markdown():
    recs = parse_records(md(CASES[:2], 0), url_pattern="euvsdisinfo")
    assert [r["title"] for r in recs] == ["Sprawa 1", "Sprawa 2"]
    assert recs[0]["fields"]["claim"] == "Twierdzenie 1 ciąg dalszy", "wartość w kilku liniach"
    assert recs[0]["url"] == "https://euvsdisinfo.eu/report/sprawa-1/"
    assert "**" not in recs[0]["content"] and "Fact Check: Fakt 1" in recs[0]["content"]


def test_parse_sse_and_plain_json():
    assert parse_sse_json(sse({"a": 1}) + sse({"b": 2})) == [{"a": 1}, {"b": 2}]
    assert parse_sse_json('{"a": 1}') == [{"a": 1}]


def test_mcp_tool_pages_backfill_and_key(tmp_path, monkeypatch):
    store = Store(tmp_path / "eu")
    monkeypatch.delenv("TEST_EUVSD_KEY", raising=False)
    with pytest.raises(ValueError, match="TEST_EUVSD_KEY"):  # brak klucza: błąd źródła bez wartości
        run_mcp_tool(Ctx(store=store, fetcher=FakeMcp()), SRC, store.source_state("eu"), Stats())
    monkeypatch.setenv("TEST_EUVSD_KEY", "SEKRET")

    fake = FakeMcp()
    st = store.source_state("eu")
    stats = Stats()
    run_mcp_tool(Ctx(store=store, fetcher=fake), SRC, st, stats)
    got = {d["url"]: d for d in docs(tmp_path / "eu")}
    # strony 0, 2, 4 (wszystko nowe) → 5 spraw; dociąganie od offsetu 2 nic nie zmienia
    assert len(got) == 5 and stats.new == 5, f"wszystkie strony nowych rekordów ({len(got)})"
    d1 = got["https://euvsdisinfo.eu/report/sprawa-1/"]
    assert d1["title"] == "Sprawa 1" and d1["published_at"].startswith("2026-11-19")
    assert d1["fetch_method"] == "mcp" and "SEKRET" not in json.dumps(d1)
    inits = [c for c in fake.calls if c[0]["method"] == "initialize"]
    assert len(inits) == 1, "po 400 dla sesji bezstanowej jeden initialize"

    # drugi przebieg: pierwsza strona bez zmian → koniec części „najnowsze”
    fake2 = FakeMcp()
    stats2 = Stats()
    run_mcp_tool(Ctx(store=store, fetcher=fake2), SRC, st, stats2)
    offsets = [c[0]["params"]["arguments"]["offset"] for c in fake2.calls if c[0]["method"] == "tools/call"]
    assert stats2.new == 0 and offsets[:2] == [0, 0], f"stop na stronie bez zmian ({offsets})"
    assert st.get("backfill_done") or st.get("backfill_offset", 0) > 2, "kursor archiwum przesuwa się"


def test_mcp_tool_401_is_error(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_EUVSD_KEY", "ZLY")
    store = Store(tmp_path / "eu401")
    with pytest.raises(HttpError, match="401"):
        run_mcp_tool(Ctx(store=store, fetcher=FakeMcp()), SRC, store.source_state("eu"), Stats())
