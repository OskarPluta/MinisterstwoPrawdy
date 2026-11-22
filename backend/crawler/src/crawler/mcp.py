"""Adapter `mcp_tool`: rekordy z narzędzia serwera MCP (Streamable HTTP, JSON-RPC 2.0), np. EUvsDisinfo.

Narzędzie zwraca tekst w Markdown z wieloma rekordami („**Case 1: …**”, pola „**Claim:** …”). Dzielimy go na
bloki (`record_split`), z bloku bierzemy tytuł, pola i adres. Każdy rekord to dokument o adresie z rekordu.
Stronicowanie przez `offset` / `limit`: zwykły przebieg czyta od najnowszych, aż trafi na stronę bez zmian;
osobny kursor `backfill_offset` w stanie dociąga archiwum po `backfill_pages_per_run` stron na przebieg.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any

from . import __version__
from .adapters import Ctx, SkipItem, Stats, _private_headers, save_doc
from .extract import parse_any_date, to_iso
from .fetcher import HttpError

log = logging.getLogger(__name__)

ACCEPT = "application/json, text/event-stream"
PROTOCOL_VERSION = "2025-03-26"
DEFAULT_SPLIT = r"^\s*(?:#+\s*)?\*\*(?:Case|Article|Result)\s+\d+"
FIELD_RE = re.compile(r"^\s*(?:[-*]\s+)?\*\*([^*\n:]{1,40}):\*\*\s*(.*)$")
LINK_RE = re.compile(r"https?://[^\s)\]>\"'*]+")


class McpError(Exception):
    """Błąd JSON-RPC albo narzędzia (isError)."""


def parse_sse_json(text: str) -> list[dict]:
    """Obiekty JSON z odpowiedzi: zwykły JSON albo strumień SSE (linie `data:`)."""
    text = text.strip()
    if text.startswith("{") or text.startswith("["):
        data = json.loads(text)
        return data if isinstance(data, list) else [data]
    out, buf = [], []
    for line in text.splitlines() + [""]:
        if line.startswith("data:"):
            buf.append(line[5:].lstrip())
        elif not line.strip() and buf:
            try:
                out.append(json.loads("\n".join(buf)))
            except json.JSONDecodeError:
                pass
            buf = []
    return out


class McpClient:
    """Minimalny klient MCP przez Fetcher (robots.txt, odstęp, wycofanie). Najpierw tryb bezstanowy
    (losowy mcp-session-id); jeśli serwer odrzuci sesję (400/404), robi `initialize`."""

    def __init__(self, ctx: Ctx, endpoint: str, private: dict | None):
        self.ctx, self.endpoint, self.private = ctx, endpoint, private
        self.session = str(uuid.uuid4())
        self.initialized = False
        self._id = 0

    def _post(self, method: str, params: dict | None, notify: bool = False):
        body: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            body["params"] = params
        if not notify:
            self._id += 1
            body["id"] = self._id
        return self.ctx.fetcher.http_send("POST", self.endpoint, json_body=body, accept=ACCEPT,
                                          headers={"mcp-session-id": self.session}, private=self.private)

    def _initialize(self) -> None:
        resp = self._post("initialize", {"protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                                         "clientInfo": {"name": "FaktBot", "version": __version__}})
        sid = {k.lower(): v for k, v in (resp.headers or {}).items()}.get("mcp-session-id")
        if sid:
            self.session = sid
        self._post("notifications/initialized", None, notify=True)
        self.initialized = True

    def call_tool(self, name: str, arguments: dict) -> str:
        """Tekst wyniku narzędzia (połączone elementy `content` typu text)."""
        try:
            resp = self._post("tools/call", {"name": name, "arguments": arguments})
        except HttpError as e:
            if e.status not in (400, 404) or self.initialized:
                raise
            self._initialize()
            resp = self._post("tools/call", {"name": name, "arguments": arguments})
        for msg in parse_sse_json(resp.text):
            if "error" in msg:
                err = msg["error"] or {}
                raise McpError(f"MCP {err.get('code')}: {str(err.get('message'))[:200]}")
            if "result" in msg:
                res = msg["result"] or {}
                text = "\n".join(c.get("text", "") for c in res.get("content") or [] if c.get("type") == "text")
                if res.get("isError"):
                    raise McpError(f"narzędzie {name}: {text[:200]}")
                return text
        raise McpError(f"brak wyniku JSON-RPC w odpowiedzi narzędzia {name}")


def _clean(s: str) -> str:
    s = re.sub(r"\*\*|__", "", s)
    s = re.sub(r"\[([^\]]*)\]\((https?://[^)]+)\)", r"\1 (\2)", s)  # link Markdown → tekst (adres)
    return re.sub(r"[ \t]+", " ", s).strip()


def parse_records(text: str, split: str = DEFAULT_SPLIT, url_pattern: str | None = None) -> list[dict]:
    """Dzieli Markdown na rekordy: {title, fields{klucz małymi literami: wartość}, url, content}."""
    starts = [m.start() for m in re.finditer(split, text, flags=re.M)]
    blocks = [text[a:b] for a, b in zip(starts, starts[1:] + [len(text)])]
    url_re = re.compile(url_pattern) if url_pattern else None
    out = []
    for block in blocks:
        lines = block.strip().splitlines()
        head = _clean(lines[0])
        title = re.sub(r"^#*\s*(?:Case|Article|Result)\s+\d+\s*[:.\-–]?\s*", "", head).strip() or None
        fields: dict[str, str] = {}
        last = None
        for line in lines[1:]:
            m = FIELD_RE.match(line)
            if m:
                last = m.group(1).strip().lower()
                fields[last] = _clean(m.group(2))
            elif last and line.strip() and not line.lstrip().startswith(("**", "#", "---")):
                fields[last] = (fields[last] + " " + _clean(line)).strip()  # wartość w kilku liniach
        links = LINK_RE.findall(block)
        url = None
        for key in ("url", "link", "source", "report"):
            if key in fields and (m := LINK_RE.search(fields[key])):
                url = m.group(0)
                break
        if not url:
            cand = [u for u in links if not url_re or url_re.search(u)]
            url = cand[0] if cand else None
        title = title or fields.get("title")
        content = "\n".join(_clean(l) for l in lines if l.strip() and not re.fullmatch(r"\s*-{3,}\s*", l))
        out.append({"title": title, "fields": fields, "url": url.rstrip(".,;") if url else None,
                    "content": content})
    return out


def run_mcp_tool(ctx: Ctx, src: dict, st: dict, stats: Stats) -> None:
    """Rekordy narzędzia MCP ze stronicowaniem offset/limit i kursorem dociągania archiwum."""
    endpoint = src["urls"][0]
    client = McpClient(ctx, endpoint, _private_headers(src))  # brak zmiennej z kluczem → błąd źródła
    tool = src["tool"]
    base_args = dict(src.get("arguments") or {})
    limit = int(src.get("page_size", 100))
    offset_param = src.get("offset_param", "offset")
    limit_param = src.get("limit_param", "limit")
    split = src.get("record_split") or DEFAULT_SPLIT
    url_pattern = src.get("url_pattern")
    date_fields = [f.lower() for f in src.get("date_fields") or ["date", "dategmt", "published"]]
    attribution = src.get("attribution")
    paginated = bool(src.get("paginate", True))

    def page(offset: int) -> list[dict]:
        args = dict(base_args)
        if paginated:
            args.update({limit_param: limit, offset_param: offset})
        return parse_records(client.call_tool(tool, args), split, url_pattern)

    def save(rec: dict) -> str:
        if not rec["url"]:
            raise SkipItem(f"rekord bez adresu: {(rec['title'] or rec['content'])[:80]!r}")
        published = None
        for f in date_fields:
            if rec["fields"].get(f) and (dt := parse_any_date(rec["fields"][f])):
                published = to_iso(dt)
                break
        content = rec["content"]
        if attribution:
            content += f"\n\n{attribution}"
        return save_doc(ctx, src, rec["url"], rec["title"], content, published, "mcp")

    def consume(records: list[dict]) -> int:
        """Zapisuje rekordy strony; zwraca liczbę nowych lub zmienionych."""
        changed = 0
        for rec in records:
            stats.found += 1
            try:
                outcome = save(rec)
                stats.count(outcome)
                changed += outcome in ("new", "updated")
            except SkipItem as e:
                stats.skipped += 1
                stats.msg(str(e))
        return changed

    # 1) najnowsze: aż do strony bez zmian (albo max_pages)
    max_pages = int(src.get("max_pages", 3)) if paginated else 1
    offset = 0
    first = True
    for _ in range(max_pages):
        records = page(offset)
        if first and not records:
            stats.status = "warn"
            stats.msg(f"narzędzie {tool} nie zwróciło rekordów (zmienił się format odpowiedzi?)")
            return
        first = False
        if not records or not consume(records) or len(records) < limit:
            break
        offset += limit

    # 2) dociąganie archiwum: kursor w stanie, kilka stron na przebieg
    if not paginated or st.get("backfill_done") or not src.get("backfill", True):
        return
    boff = int(st.get("backfill_offset", limit))
    for _ in range(int(src.get("backfill_pages_per_run", 5))):
        records = page(boff)
        if not records:
            st["backfill_done"] = True
            stats.msg(f"archiwum pobrane do końca (offset {boff})")
            break
        consume(records)
        boff += limit
        st["backfill_offset"] = boff
        if len(records) < limit:
            st["backfill_done"] = True
            break
    else:
        stats.msg(f"dociąganie archiwum: offset {boff}")
