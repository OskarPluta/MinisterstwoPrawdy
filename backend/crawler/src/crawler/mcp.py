"""Adapter `mcp_tool`: rekordy z narzędzia serwera MCP (Streamable HTTP, JSON-RPC 2.0). Dziś: EUvsDisinfo.

Format odpowiedzi narzędzi `cases` / `articles` (sprawdzony na mcp.euvsd.info, 2026-11-22): zdarzenie SSE
z JSON-RPC, w `result.content[0].text` Markdown:

    Returning 2 cases (offset 0)
    **Case 1: <tytuł>**
    **ID:** <uuid>
    **Claim:**
    <tekst w kolejnych liniach>
    **Date:** 11/16/2026            (artykuły: 2026-11-20 10:08:54)
    **Source URL:** https://euvsdisinfo.eu/report/...
    ---
    **Database Info:**              (artykuły: **Articles Info:**)
    - Total cases: 19873
    - Has next page: Yes

Każdy rekord to dokument o adresie z `Source URL`. Zwykły przebieg czyta od najnowszych, aż trafi na stronę
bez zmian; kursor `backfill_offset` w stanie dociąga archiwum po `backfill_pages_per_run` stron na przebieg.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from urllib.parse import urlsplit
from datetime import datetime, timezone

from .adapters import Ctx, Stats, _private_headers, save_doc
from .extract import to_iso
from .fetcher import Resp

log = logging.getLogger(__name__)

ACCEPT = "application/json, text/event-stream"
RECORD_RE = re.compile(r"^\*\*(?:Case|Article) \d+: (.+?)\*\*\s*$", re.M)
FIELD_RE = re.compile(r"^\*\*([^*\n]{1,40}):\*\*[ \t]*(.*)$")
INFO_RE = re.compile(r"^\*\*(?:Database|Articles|Cases) Info:\*\*", re.M)
DATE_FORMATS = ("%m/%d/%Y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d")


class McpError(Exception):
    """Błąd JSON-RPC albo narzędzia (isError) albo odpowiedź bez wyniku."""


def tool_text(resp: Resp) -> str:
    """Tekst wyniku narzędzia z odpowiedzi SSE (`data: {...}`) albo zwykłego JSON."""
    raw = resp.text.strip()
    if raw.startswith("{"):
        msgs = [json.loads(raw)]
    else:
        msgs = [json.loads(line[5:]) for line in raw.splitlines() if line.startswith("data:")]
    for msg in msgs:
        if msg.get("error"):
            raise McpError(f"MCP {msg['error'].get('code')}: {str(msg['error'].get('message'))[:200]}")
        if "result" in msg:
            res = msg["result"] or {}
            text = "\n".join(c.get("text", "") for c in res.get("content") or [] if c.get("type") == "text")
            if res.get("isError"):
                raise McpError(f"błąd narzędzia: {text[:200]}")
            return text
    raise McpError("brak wyniku JSON-RPC w odpowiedzi")


def parse_records(text: str) -> tuple[list[dict], bool | None]:
    """(rekordy, czy jest następna strona). Rekord: {title, fields{etykieta: wartość}, body}."""
    info = INFO_RE.search(text)
    has_next = None
    if info:
        m = re.search(r"Has next page:\s*(Yes|No)", text[info.start():])
        has_next = m.group(1) == "Yes" if m else None
        text = text[:info.start()]
    heads = list(RECORD_RE.finditer(text))
    records = []
    for h, nxt in zip(heads, heads[1:] + [None]):
        block = text[h.end():nxt.start() if nxt else len(text)]
        fields: dict[str, list[str]] = {}
        order: list[str] = []
        cur = None
        for line in block.splitlines():
            if re.fullmatch(r"\s*-{3,}\s*", line):
                continue
            m = FIELD_RE.match(line)
            if m:
                cur = m.group(1).strip()
                order.append(cur)
                fields[cur] = [m.group(2).strip()] if m.group(2).strip() else []
            elif cur and line.strip():
                fields[cur].append(line.rstrip())
        flat = {k: "\n".join(v).strip() for k, v in fields.items()}
        body = "\n".join(f"{k}: {flat[k]}" if "\n" not in flat[k] else f"{k}:\n{flat[k]}" for k in order)
        records.append({"title": h.group(1).strip(), "fields": flat, "body": body})
    return records, has_next


def parse_date(value: str | None) -> str | None:
    for fmt in DATE_FORMATS:
        try:
            return to_iso(datetime.strptime((value or "").strip(), fmt).replace(tzinfo=timezone.utc))
        except ValueError:
            continue
    return None


def run_mcp_tool(ctx: Ctx, src: dict, st: dict, stats: Stats) -> None:
    endpoint = src["urls"][0]
    private = _private_headers(src)  # brak zmiennej z kluczem → błąd źródła (bez wartości w komunikacie)
    session = str(uuid.uuid4())      # tryb bezstanowy: serwer przyjmuje dowolny identyfikator sesji
    tool = src["tool"]
    limit = int(src.get("page_size", 100))
    allowed = set(src.get("allowed_hosts") or [])
    calls = 0

    def page(offset: int) -> tuple[list[dict], bool | None]:
        nonlocal calls
        calls += 1
        body = {"jsonrpc": "2.0", "id": calls, "method": "tools/call",
                "params": {"name": tool, "arguments": {"limit": limit, "offset": offset}}}
        resp = ctx.fetcher.http_send("POST", endpoint, json_body=body, accept=ACCEPT,
                                     headers={"mcp-session-id": session}, private=private)
        return parse_records(tool_text(resp))

    def consume(records: list[dict]) -> int:
        changed = 0
        for rec in records:
            stats.found += 1
            url = rec["fields"].get("Source URL", "").strip()
            if not url.startswith(("http://", "https://")):
                stats.skipped += 1
                stats.msg(f"rekord bez Source URL: {rec['title'][:80]!r}")
                continue
            if allowed and urlsplit(url).hostname not in allowed:  # np. kopie z serwera preprod EUvsDisinfo
                stats.skipped += 1
                stats.msg(f"pominięto adres spoza allowed_hosts: {urlsplit(url).hostname}")
                continue
            outcome = save_doc(ctx, src, url, rec["title"], rec["body"], parse_date(rec["fields"].get("Date")), "mcp")
            stats.count(outcome)
            changed += outcome in ("new", "updated")
        return changed

    # 1) najnowsze: do pierwszej strony bez zmian (najwyżej max_pages)
    offset = 0
    for i in range(int(src.get("max_pages", 3))):
        records, has_next = page(offset)
        if i == 0 and not records:
            stats.status = "warn"
            stats.msg(f"narzędzie {tool} nie zwróciło rekordów – możliwa zmiana formatu odpowiedzi")
            return
        if not consume(records) or has_next is False:
            break
        offset += limit

    # 2) archiwum: kursor w stanie, backfill_pages_per_run stron na przebieg, do „Has next page: No”
    if st.get("backfill_done"):
        return
    boff = int(st.get("backfill_offset", offset + limit))  # pierwszy raz: za stronami przeczytanymi wyżej
    for _ in range(int(src.get("backfill_pages_per_run", 10))):
        records, has_next = page(boff)
        consume(records)
        boff += limit
        st["backfill_offset"] = boff
        if not records or has_next is False:
            st["backfill_done"] = True
            stats.msg(f"archiwum pobrane do końca ({boff} rekordów)")
            return
    stats.msg(f"dociąganie archiwum: offset {boff}")
