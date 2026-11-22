"""Live lookup in trusted sources when the local corpus cannot settle a claim.

When to look (rule): a checkable claim whose first-pass verdict is `insufficient` (no hits at
all, or the model found nothing that settles it, or a verdict downgraded for lack of citations).
One lookup per check (the first such claim's wording plus the other claims' first query); at most
MAX_LIVE_CLAIMS claims are then re-checked on its results. `PRAWDOWID_LIVE=0` turns it off.

Where to look (only these, all at once, within BUDGET_S seconds):
  - Google Fact Check Tools `claims:search` (ClaimReview of fact-checkers on TRUSTED_FACTCHECK_DOMAINS),
  - EUvsDisinfo MCP tool `search` (only euvsdisinfo.eu addresses),
  - gov.pl site search (`/api/data/search`, the endpoint the portal's own search page calls), then the
    top GOVPL_MAX_ARTICLES articles on www.gov.pl, extracted with the crawler's gov.pl profile.

How: every request goes through `crawler.fetcher.Fetcher` with the honest FaktBot User-Agent, so
robots.txt is checked, hosts are spaced by at least 3 s, and 429/503/403 or a CAPTCHA stop the
source (no retries, no workarounds). One Fetcher per source lives for the whole process, so the
spacing, robots cache and backoff also hold between consecutive checks (in the API server); backoff
is kept in memory, not in the crawler's state.json. Trade-off: gov.pl costs robots.txt (a redirect,
so 2 requests, cached afterwards) + search + 2 articles, about 12 s with the 3 s spacing on a cold
start and 9 s warm; it is the slowest source and sets the latency of the live step.

Results become ordinary versioned records via `ingest_corpus` (source ids `live-factcheck-<domain>`,
`live-euvsdisinfo`, `live-govpl-<unit>`: one source per publishing domain/unit, because a record must
cite a URL on its source's domain). A URL already in the database under a crawler source is reused,
not duplicated. The verifier then re-checks the claim on a shortlist of the live passages (ranked by
similarity to the claim) plus the first-pass local passages. Links still come only from the stored
`citation_url`. The lookup log (counts, errors, source URLs; no user text) is kept in
`ClaimResult.live`, so it lands in the `checks` table; the user sees a note.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from collections import Counter
from collections.abc import Callable
import functools
import math
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import wait as wait_futures
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode, urljoin, urlsplit

import numpy as np

from .database import query
from .db import Connection
from .embed import Embedder, _fold
from .ingest import ingest_corpus
from .search import STOPWORDS, Hit, passage_meta, query_tokens
from .timeutil import parse_ts, utcnow_iso

if TYPE_CHECKING:
    from datetime import datetime

    from .llm import Claim
    from .verify import ClaimResult, Verifier

log = logging.getLogger(__name__)

LIVE_ENV = "PRAWDOWID_LIVE"
BUDGET_S = 18.0          # wall clock for all sources of one claim (a whole check should stay under ~30 s)
HTTP_TIMEOUT_S = 6.0     # per request
MAX_LIVE_CLAIMS = 2      # claims per check re-checked on live evidence (one shared lookup per check)
FC_MAX_REQUESTS = 3
FC_QUERY_WORDS = 3
FC_MAX_DOCS = 5
EUVSD_MAX_REQUESTS = 2
EUVSD_MAX_CASES = 10    # short Claim Review records (5 per answer)
EUVSD_MAX_ARTICLES = 2  # long analytical articles
GOVPL_SEARCH_SIZE = 25
GOVPL_MAX_ARTICLES = 2
LIVE_IN_SHORTLIST = 6    # live passages in the second-pass shortlist (the rest: first-pass local hits)

FC_ENDPOINT = "https://factchecktools.googleapis.com/v1alpha1/claims:search"
EUVSD_ENDPOINT = "https://mcp.euvsd.info/mcp"
EUVSD_HOSTS = {"euvsdisinfo.eu", "www.euvsdisinfo.eu"}
EUVSD_PUBLISHER = "EUvsDisinfo (Europejska Służba Działań Zewnętrznych)"
GOVPL_BASE = "https://www.gov.pl"
GOVPL_SEARCH = GOVPL_BASE + "/api/data/search"
# The crawler's gov.pl profile (backend/crawler/sources.yaml, x-govpl).
GOVPL_PROFILE = {
    "title_selector": "article h2",
    "content_selector": "article .editor-content",
    "drop_selectors": [".metrics", ".metric-history", ".gallery", ".main-photo"],
}

# Fact-checkers whose ClaimReview we accept (IFCN/EFCSN signatories and the Polish desks the crawler
# already uses). A review on any other domain is dropped. Subdomains match (factcheck.afp.com).
TRUSTED_FACTCHECK_DOMAINS = (
    "demagog.org.pl", "sprawdzam.afp.com", "fakenews.pl", "oko.press", "konkret24.tvn24.pl",
    "pravda.org.pl", "afp.com", "reuters.com", "apnews.com", "fullfact.org", "correctiv.org",
    "politifact.com", "factcheck.org", "leadstories.com", "snopes.com", "maldita.es", "newtral.es",
    "euvsdisinfo.eu", "stopfake.org", "demagog.cz", "demagog.sk", "dpa-factchecking.com",
    "faktisk.no", "healthfeedback.org", "sciencefeedback.co", "climatefeedback.org", "africacheck.org",
    "chequeado.com", "aosfatos.org", "teyit.org", "factly.in", "boomlive.in", "lemonde.fr",
    "liberation.fr", "bbc.co.uk", "bbc.com", "usatoday.com", "washingtonpost.com", "voxukraine.org",
    "lrt.lt", "delfi.lt", "re-baltica.lv", "propastop.org", "mythdetector.com", "factcheck.ge",
)

LABELS = {"factcheck": "Google Fact Check", "euvsdisinfo": "EUvsDisinfo", "govpl": "gov.pl"}


def live_enabled() -> bool:
    return os.environ.get(LIVE_ENV, "1").strip().lower() not in ("0", "false", "no", "off")


class LiveSkip(Exception):
    """The source is not configured (no API key, no contact address)."""


class OutOfTime(Exception):
    pass


# ---------------------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------------------


@dataclass
class SourceRun:
    key: str
    sources: list[dict[str, Any]] = field(default_factory=list)  # catalog rows
    records: list[dict[str, Any]] = field(default_factory=list)  # evidence records
    requests: int = 0
    error: str | None = None    # failed or cut short (records found so far are still used)
    skipped: str | None = None  # not configured
    duration_ms: int = 0

    @property
    def label(self) -> str:
        return LABELS.get(self.key, self.key)


@dataclass
class LiveResult:
    runs: list[SourceRun] = field(default_factory=list)
    evidence_ids: list[int] = field(default_factory=list)  # live records, new and already known
    actions: Counter[str] = field(default_factory=Counter)
    reused: int = 0  # URLs already in the database under a crawler source
    duration_ms: int = 0

    @property
    def new_docs(self) -> int:
        return self.actions["inserted"] + self.actions["new_version"]

    def note(self) -> str | None:
        """User-facing note (Polish). None when no source was even queried."""
        asked = [r for r in self.runs if not r.skipped]
        if not asked:
            return None
        found = ", ".join(f"{r.label} ({len(r.records)})" for r in asked)
        note = f"Wyszukano na żywo w zaufanych źródłach: {found}."
        if not self.evidence_ids:
            note += " Nie znaleziono tam materiałów na ten temat."
        failed = [r.label for r in asked if r.error]
        if failed:
            note += f" Nie wszystkie odpowiedziały: {', '.join(failed)}."
        return note

    def to_log(self) -> dict[str, Any]:
        """For the checks log: counts, errors and source URLs (no user text)."""
        return {
            "sources": [
                {"source": r.key, "requests": r.requests, "found": len(r.records), "error": r.error,
                 "skipped": r.skipped, "duration_ms": r.duration_ms,
                 "urls": [rec["citation_url"] for rec in r.records]}
                for r in self.runs
            ],
            "evidence_ids": self.evidence_ids,
            "actions": dict(self.actions),
            "reused": self.reused,
            "new_docs": self.new_docs,
            "duration_ms": self.duration_ms,
        }


# ---------------------------------------------------------------------------------------
# HTTP through the crawler's Fetcher
# ---------------------------------------------------------------------------------------


class _MemoryStore:
    """What Fetcher needs from crawler.storage.Store, kept in memory: backoff per host and
    (unused) HTTP validators. The crawler's own state.json is never touched."""

    def __init__(self) -> None:
        self.state: dict[str, Any] = {"backoff": {}, "http_cache": {}, "sources": {}}

    def save_state(self) -> None:
        pass

    def validators(self, url: str) -> dict:
        return {}

    def set_validators(self, url: str, etag: str | None, last_modified: str | None) -> None:
        pass


def crawler_config_path() -> Path:
    env = os.environ.get("CRAWLER_CONFIG")
    if env:
        return Path(env)
    import crawler

    return Path(crawler.__file__).resolve().parents[2] / "sources.yaml"  # backend/crawler/sources.yaml


def user_agent() -> str:
    """`FaktBot/<version> (+contact)`, contact from CRAWLER_CONTACT or sources.yaml settings.contact."""
    import yaml
    from crawler.config import SETTINGS_DEFAULTS, Config, ConfigError

    path = crawler_config_path()
    settings = dict(SETTINGS_DEFAULTS)
    try:
        settings.update((yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("settings") or {})
    except OSError:
        pass  # CRAWLER_CONTACT alone is enough
    try:
        return Config(settings=settings, sources=[], path=path).user_agent()
    except ConfigError as e:
        raise LiveSkip(str(e)) from None


def make_fetcher() -> Any:
    from crawler.config import SETTINGS_DEFAULTS
    from crawler.fetcher import Fetcher

    settings = {**SETTINGS_DEFAULTS, "http_timeout_seconds": HTTP_TIMEOUT_S}
    return Fetcher(settings, _MemoryStore(), user_agent())  # type: ignore[arg-type]


_FETCHERS: dict[str, tuple[Any, threading.Lock]] = {}
_FETCHERS_LOCK = threading.Lock()


def _shared_fetcher(key: str) -> tuple[Any, threading.Lock]:
    """One Fetcher per source for the life of the process (robots cache, spacing, backoff)."""
    with _FETCHERS_LOCK:
        if key not in _FETCHERS:
            _FETCHERS[key] = (make_fetcher(), threading.Lock())
        return _FETCHERS[key]


class _Budget:
    def __init__(self, run: SourceRun, deadline: float):
        self.run, self.deadline = run, deadline

    def spend(self) -> None:
        """Call before each request: counts it and stops the source when time is up.
        The margin covers the 3 s host spacing the Fetcher may still wait."""
        if time.monotonic() > self.deadline - 3.5:
            raise OutOfTime("budżet czasu wyczerpany")
        self.run.requests += 1


def _host_ok(url: str, domains: tuple[str, ...] | set[str]) -> str | None:
    """The matched trusted domain for an https URL, else None."""
    u = urlsplit(url or "")
    host = (u.hostname or "").lower()
    if u.scheme != "https":
        return None
    for d in domains:
        if host == d or host.endswith("." + d):
            return d
    return None


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", _fold(s)).strip("-")


def _utc(value: str | None) -> str | None:
    dt = parse_ts(value)
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z") if dt else None


def _err(e: BaseException) -> str:
    """Error for the log without URLs (they may carry the user's query)."""
    status = getattr(e, "status", None)
    return f"{type(e).__name__}" + (f" HTTP {status}" if status else "")


# ---------------------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------------------


def factcheck_records(data: dict[str, Any], fetched_at: str) -> tuple[list[dict], list[dict]]:
    """claims:search JSON -> (sources, records); one record per review on a trusted domain."""
    sources: dict[str, dict] = {}
    records: list[dict] = []
    for c in data.get("claims") or []:
        for rv in c.get("claimReview") or []:
            url = (rv.get("url") or "").strip()
            host = (urlsplit(url).hostname or "").lower()
            if not _host_ok(url, TRUSTED_FACTCHECK_DOMAINS):
                continue
            domain = host.removeprefix("www.")
            sid = f"live-factcheck-{_slug(domain)}"
            pub = (rv.get("publisher") or {}).get("name") or domain
            sources.setdefault(sid, {
                "source_id": sid, "publisher": pub, "publishing_domain": domain, "source_type": "factcheck",
                "citation_url": f"https://{host}/", "terms_url": None,
                "refresh_policy": "na żywo (Google Fact Check Tools)", "last_success_at": fetched_at,
                "last_error": None,
            })
            lines = [f"Twierdzenie: {c['text'].strip()}"] if c.get("text") else []
            if c.get("claimant"):
                lines.append(f"Autor twierdzenia: {c['claimant']}")
            if c.get("claimDate"):
                lines.append(f"Data twierdzenia: {c['claimDate'][:10]}")
            if rv.get("textualRating"):
                lines.append(f"Ocena {pub}: {rv['textualRating']}")
            if rv.get("title"):
                lines.append(f"Tytuł weryfikacji: {rv['title']}")
            if rv.get("reviewDate"):
                lines.append(f"Data weryfikacji: {rv['reviewDate'][:10]}")
            records.append({
                "source_id": sid, "upstream_id": url, "kind": "factcheck",
                "title": (rv.get("title") or c.get("text") or url).strip(),
                "citation_url": url, "published_at": _utc(rv.get("reviewDate") or c.get("claimDate")),
                "fetched_at": fetched_at, "sections": [{"heading": None, "text": "\n\n".join(lines)}],
            })
    return list(sources.values()), records


def _short_query(q: str, n: int | None = None) -> str:
    """The first `n` content words of a query (stopwords dropped), in their original form."""
    words = [w for w in re.findall(r"[\w-]+", q) if len(w) > 1 and _fold(w) not in STOPWORDS]
    return " ".join(words[: n or FC_QUERY_WORDS])


def search_factcheck(fetcher: Any, budget: _Budget, claim_text: str, queries: list[str]) -> SourceRun:
    run = budget.run
    key = os.environ.get("GOOGLE_FACTCHECK_API_KEY")
    if not key:
        raise LiveSkip("brak GOOGLE_FACTCHECK_API_KEY")
    # The API wants every word, in the form used in the reviewed claim (checked 2026-11-22: 5 keywords
    # or "awarie turbin wiatrowych" -> 0 results, "turbiny wiatrowe psują" -> the review), so queries are
    # cut to FC_QUERY_WORDS words: the claim's own wording first, then the keyword queries. The last
    # attempt drops languageCode, which also returns reviews in other languages or without a language tag.
    shorts = [s for s in dict.fromkeys(_short_query(q) for q in [claim_text, *queries]) if s]
    attempts = [(s, "pl") for s in shorts[:2]] + ([(shorts[2] if len(shorts) > 2 else shorts[0], None)] if shorts else [])
    attempts = list(dict.fromkeys(attempts))[:FC_MAX_REQUESTS]
    seen: set[str] = set()
    for q, lang in attempts:
        params = {"query": q[:200], "pageSize": 10, **({"languageCode": lang} if lang else {})}
        budget.spend()
        resp = fetcher.http_get(f"{FC_ENDPOINT}?{urlencode(params)}", conditional=False,
                                accept="application/json", private={"X-Goog-Api-Key": key})
        srcs, recs = factcheck_records(json.loads(resp.text), utcnow_iso())
        for s in srcs:
            if s["source_id"] not in {x["source_id"] for x in run.sources}:
                run.sources.append(s)
        for r in recs:
            if r["citation_url"] not in seen and len(run.records) < FC_MAX_DOCS:
                seen.add(r["citation_url"])
                run.records.append(r)
        if run.records:
            break
    return run


_EUVSD_ITEM = re.compile(r"^\*\*Item \d+: (.+?)\s*\*\*\s*$", re.M)
_EUVSD_TYPE = re.compile(r"^\*\*Type:\s*(.+?)\s*\*\*\s*$", re.M)
_EUVSD_URL = re.compile(r"^\*\*Source URL:\*\*\s*(\S+)\s*$", re.M)
_EUVSD_INFO = re.compile(r"^\*\*Results Info:\*\*", re.M)


def parse_euvsd_search(text: str) -> list[dict[str, str]]:
    """Markdown of the `search` tool (format checked on mcp.euvsd.info, 2026-11-22):

        **Item 1: <title>**
        **Type: Article** | **Type: Claim Review**
        **Content:**
        <text; Claim Review: "SUMMARY\\n...\\nRESPONSE\\n..." with RESPONSE often empty>
        **Source URL:** https://euvsdisinfo.eu/...
        ---
        **Results Info:** ...
    """
    info = _EUVSD_INFO.search(text)
    if info:
        text = text[: info.start()]
    heads = list(_EUVSD_ITEM.finditer(text))
    items = []
    for h, nxt in zip(heads, [*heads[1:], None]):
        block = text[h.end(): nxt.start() if nxt else len(text)]
        typ = _EUVSD_TYPE.search(block)
        url = _EUVSD_URL.search(block)
        start = block.find("**Content:**")
        end = url.start() if url else len(block)
        content = block[start + len("**Content:**"): end] if start >= 0 else ""
        content = re.sub(r"\n\s*-{3,}\s*$", "", content.strip()).strip()
        items.append({"title": h.group(1).strip(), "type": typ.group(1).strip() if typ else "",
                      "content": content, "url": url.group(1).strip() if url else ""})
    return items


def _paragraphs(text: str) -> str:
    return "\n\n".join(ln.strip() for ln in text.splitlines() if ln.strip())


def euvsd_records(items: list[dict[str, str]], fetched_at: str) -> list[dict]:
    records = []
    for it in items:
        if not _host_ok(it["url"], EUVSD_HOSTS) or not it["content"]:
            continue
        if it["type"].lower().startswith("claim"):
            summary, _, response = it["content"].partition("\nRESPONSE")
            summary = summary.removeprefix("SUMMARY").strip()
            if not summary:
                continue
            sections = [{"heading": "Przypadek dezinformacji w bazie EUvsDisinfo – SUMMARY",
                         "text": _paragraphs(summary)}]
            if response.strip():
                sections.append({"heading": "Przypadek dezinformacji w bazie EUvsDisinfo – RESPONSE",
                                 "text": _paragraphs(response)})
            kind = "factcheck"
        else:
            sections = [{"heading": None, "text": _paragraphs(it["content"])}]
            kind = "article"
        records.append({
            "source_id": "live-euvsdisinfo", "upstream_id": it["url"], "kind": kind, "title": it["title"],
            "citation_url": it["url"], "published_at": None, "fetched_at": fetched_at, "sections": sections,
        })
    return records


def search_euvsdisinfo(fetcher: Any, budget: _Budget, claim_text: str, queries: list[str]) -> SourceRun:
    import uuid

    from crawler.mcp import ACCEPT, tool_text

    run = budget.run
    key = os.environ.get("EUVSDISINFO_API_KEY")
    if not key:
        raise LiveSkip("brak EUVSDISINFO_API_KEY")
    # The tool answers the 5 most relevant articles and the 5 most relevant cases for any query
    # (semantic search, Polish works). Two queries (the claim, then a keyword query) widen the cases.
    asked = [q for q in dict.fromkeys([claim_text, *queries[:1]]) if len(q.strip()) >= 3][:EUVSD_MAX_REQUESTS]
    found: list[dict] = []
    for i, q in enumerate(asked, 1):
        body = {"jsonrpc": "2.0", "id": i, "method": "tools/call",
                "params": {"name": "search", "arguments": {"query": q[:300]}}}
        budget.spend()
        resp = fetcher.http_send("POST", EUVSD_ENDPOINT, json_body=body, accept=ACCEPT,
                                 headers={"mcp-session-id": str(uuid.uuid4())},  # stateless mode
                                 private={"Authorization": f"Bearer {key}"})
        found += euvsd_records(parse_euvsd_search(tool_text(resp)), utcnow_iso())
    unique = list({r["citation_url"].rstrip("/"): r for r in reversed(found)}.values())[::-1]  # first wins
    cases = [r for r in unique if r["kind"] == "factcheck"][:EUVSD_MAX_CASES]
    articles = [r for r in unique if r["kind"] != "factcheck"][:EUVSD_MAX_ARTICLES]
    run.records = cases + articles
    if run.records:
        run.sources = [{
            "source_id": "live-euvsdisinfo", "publisher": EUVSD_PUBLISHER, "publishing_domain": "euvsdisinfo.eu",
            "source_type": "factcheck", "citation_url": "https://euvsdisinfo.eu/", "terms_url": None,
            "refresh_policy": "na żywo (serwer MCP EUvsDisinfo, narzędzie search)",
            "last_success_at": utcnow_iso(), "last_error": None,
        }]
    return run


def govpl_pick(results: list[dict[str, Any]], q: str, n: int = GOVPL_MAX_ARTICLES,
               embedder: Embedder | None = None, claim: str | None = None) -> list[dict[str, Any]]:
    """Search results worth fetching: www.gov.pl /web/ pages whose title+intro share query words.

    The portal search ORs the words, so many results share only common words ("sezon", "2026").
    Ranking: word overlap weighted by rarity within the result set (IDF: a word found in few results,
    e.g. "grypie", says more), plus – with a semantic embedder – the min-max normalised similarity to
    the claim. Ties: newest first."""
    toks = list(dict.fromkeys(t[:5] for t in query_tokens(q)))
    need = min(2, len(toks))
    cands, seen = [], set()
    for r in results:
        url = r.get("url") or ""
        key = url.rstrip("/").lower()
        if not url.startswith("/web/") or key in seen:
            continue
        seen.add(key)
        hay = _fold(f"{r.get('title') or ''} {r.get('intro') or ''}")
        hits = {t for t in toks if t in hay}
        if toks and len(hits) >= need:
            cands.append((hits, r))
    if not cands:
        return []
    df = {t: sum(1 for h, _ in cands if t in h) for t in toks}
    idf = {t: math.log((len(cands) + 1) / (df[t] + 0.5)) for t in toks}
    total = sum(idf.values()) or 1.0
    lex = [sum(idf[t] for t in h) / total for h, _ in cands]
    sem = [0.0] * len(cands)
    # The hash embedder (tests, offline) carries no meaning, so it never reorders the results.
    if embedder is not None and claim and len(cands) > n and not embedder.name.startswith("hash-"):
        qv = np.asarray(embedder.embed_query(claim))
        pv = np.asarray(embedder.embed_passages([f"{r.get('title') or ''}. {r.get('intro') or ''}" for _, r in cands]))
        sims = pv @ qv / (np.linalg.norm(pv, axis=1) * np.linalg.norm(qv) + 1e-9)
        lo, hi = float(sims.min()), float(sims.max())
        sem = [(float(x) - lo) / (hi - lo) if hi > lo else 0.0 for x in sims]
    order = sorted(range(len(cands)), key=lambda i: (lex[i] + sem[i], cands[i][1].get("publishDate") or ""),
                   reverse=True)
    return [cands[i][1] for i in order[:n]]


def govpl_publisher(html: str, unit: str) -> str:
    """Unit name from '<title>Article - Unit name - Portal Gov.pl</title>'."""
    m = re.search(r"<title>(.*?)</title>", html, re.S | re.I)
    parts = [p.strip() for p in (m.group(1) if m else "").split(" - ")]
    if len(parts) >= 3 and parts[-1] == "Portal Gov.pl" and parts[-2]:
        return parts[-2]
    return f"Portal Gov.pl ({unit})"


def search_govpl(fetcher: Any, budget: _Budget, claim_text: str, queries: list[str],
                 embedder: Embedder | None = None) -> SourceRun:
    from crawler.extract import extract_article
    from crawler.fetcher import HttpError

    run = budget.run
    # The claim's own wording first (the portal ranks a full sentence better than the LLM's keyword
    # query), the keyword query only when the first search leaves too few candidates.
    asked = [x for x in dict.fromkeys([claim_text, *queries[:1]]) if x and x.strip()]
    words = " ".join(asked)
    picks: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    for i, q in enumerate(asked):
        if i and len(picks) >= GOVPL_MAX_ARTICLES:
            break
        budget.spend()
        resp = fetcher.http_get(f"{GOVPL_SEARCH}?{urlencode({'query': q[:200], 'page': 1, 'size': GOVPL_SEARCH_SIZE})}",
                                conditional=False, accept="application/json")
        results += json.loads(resp.text).get("results") or []
        picks = govpl_pick(results, words, embedder=embedder, claim=claim_text)
    for r in picks:
        url = urljoin(GOVPL_BASE, r["url"])
        budget.spend()
        try:
            page = fetcher.http_get(url, conditional=False, accept="text/html,application/xhtml+xml")
        except HttpError as e:
            if e.status in (404, 410):  # stale search index entry; throttling or a block still stops the source
                continue
            raise
        if (urlsplit(page.final_url).hostname or "").lower() != "www.gov.pl":
            continue
        ex = extract_article(page.text, page.final_url, **GOVPL_PROFILE)
        if ex.method != "selector":  # empty .editor-content (e.g. text only in an attachment): the
            continue                 # fallbacks would return page chrome, not the article
        parts = urlsplit(page.final_url).path.split("/")  # /web/<unit>/<slug>
        unit = parts[2] if len(parts) > 2 and parts[1] == "web" and parts[2] else "gov"
        sid = f"live-govpl-{_slug(unit)}"
        if sid not in {x["source_id"] for x in run.sources}:
            run.sources.append({
                "source_id": sid, "publisher": govpl_publisher(page.text, unit), "publishing_domain": "gov.pl",
                "source_type": "official", "citation_url": f"{GOVPL_BASE}/web/{unit}", "terms_url": None,
                "refresh_policy": "na żywo (wyszukiwarka gov.pl)", "last_success_at": utcnow_iso(),
                "last_error": None,
            })
        title = ex.title or r.get("title") or url
        lines = [ln.strip() for ln in ex.text.splitlines() if ln.strip()]
        if lines and lines[0].casefold() == title.strip().casefold():
            lines = lines[1:]
        run.records.append({
            "source_id": sid, "upstream_id": page.final_url, "kind": "article", "title": title.strip(),
            "citation_url": page.final_url, "fetched_at": utcnow_iso(),
            "published_at": _utc(r.get("publishDate")) or (_utc(ex.date.isoformat()) if ex.date else None),
            "sections": [{"heading": None, "text": "\n\n".join(lines)}],
        })
    return run


SourceFn = Callable[[Any, _Budget, str, list[str]], SourceRun]
SOURCES: dict[str, SourceFn] = {
    "factcheck": search_factcheck,
    "euvsdisinfo": search_euvsdisinfo,
    "govpl": search_govpl,
}


# ---------------------------------------------------------------------------------------
# Lookup: query the sources, store the results
# ---------------------------------------------------------------------------------------


def _run_source(key: str, fn: SourceFn, deadline: float, claim_text: str, queries: list[str],
                fetcher_factory: Callable[[str], Any] | None) -> SourceRun:
    from crawler.fetcher import FetchError

    run = SourceRun(key)
    t0 = time.monotonic()
    lock = None
    try:
        if fetcher_factory is not None:
            fetcher = fetcher_factory(key)
        else:
            fetcher, lock = _shared_fetcher(key)
            if not lock.acquire(timeout=1.0):
                lock = None
                raise OutOfTime("źródło zajęte poprzednim zapytaniem")
        fn(fetcher, _Budget(run, deadline), claim_text, queries)
    except LiveSkip as e:
        run.skipped = str(e)
    except (OutOfTime, FetchError, ValueError, KeyError, TypeError) as e:
        run.error = _err(e) if not isinstance(e, OutOfTime) else f"OutOfTime: {e}"
    except Exception as e:  # noqa: BLE001 - one source must not sink the others
        run.error = _err(e)
        log.exception("live source %s failed", key)
    finally:
        if lock is not None:
            lock.release()
        run.duration_ms = int((time.monotonic() - t0) * 1000)
    return run


def _norm_url(url: str) -> str:
    u = urlsplit(url)
    return f"{(u.hostname or '').lower()}{u.path.rstrip('/')}?{u.query}"


def live_lookup(
    conn: Connection, embedder: Embedder | None, claim_text: str, query_variants: list[str],
    *, sources: dict[str, SourceFn] | None = None, budget_s: float = BUDGET_S,
    fetcher_factory: Callable[[str], Any] | None = None,
) -> LiveResult:
    """Query the trusted sources concurrently, ingest what they return (versioned, idempotent)
    and return the ids of the live records (new or already known). Network runs in worker
    threads; the database is used only from the calling thread."""
    t0 = time.monotonic()
    deadline = t0 + budget_s
    src = SOURCES if sources is None else sources
    queries = [q for q in dict.fromkeys(query_variants) if q and q.strip()]
    pool = ThreadPoolExecutor(max_workers=max(1, len(src)), thread_name_prefix="live")
    src = {k: (functools.partial(fn, embedder=embedder) if fn is search_govpl else fn) for k, fn in src.items()}
    futures = {k: pool.submit(_run_source, k, fn, deadline, claim_text, queries, fetcher_factory)
               for k, fn in src.items()}
    wait_futures(list(futures.values()), timeout=budget_s + 2)
    pool.shutdown(wait=False, cancel_futures=True)  # a source past its time is left behind, not awaited
    res = LiveResult()
    for k, f in futures.items():
        res.runs.append(f.result() if f.done() else SourceRun(k, error="OutOfTime: brak odpowiedzi"))

    # One record per URL; a URL the crawler already stored is reused instead of copied.
    records: list[dict] = []
    seen: set[str] = set()
    for r in res.runs:
        for rec in r.records:
            n = _norm_url(rec["citation_url"])
            if n not in seen:
                seen.add(n)
                records.append(rec)
    if records:
        variants = []
        for rec in records:
            u = rec["citation_url"]
            variants += [u, u.rstrip("/"), u.rstrip("/") + "/"]
        known = {_norm_url(row["citation_url"]): row
                 for row in conn.execute(query("evidence.current_by_urls"), {"urls": list(set(variants))})}
        fresh = []
        for rec in records:
            row = known.get(_norm_url(rec["citation_url"]))
            if row is not None and not row["source_id"].startswith("live-"):
                res.evidence_ids.append(row["evidence_id"])
                res.reused += 1
            else:
                fresh.append(rec)
        catalog = {s["source_id"]: s for r in res.runs for s in r.sources}
        used = {rec["source_id"] for rec in fresh}
        res.actions = ingest_corpus(
            conn, {"sources": [s for sid, s in catalog.items() if sid in used], "evidence": fresh}, embedder
        )
        for rec in fresh:
            row = conn.execute(query("evidence.find_current"),
                               {"source_id": rec["source_id"], "upstream_id": rec["upstream_id"]}).fetchone()
            if row is not None:
                res.evidence_ids.append(row["evidence_id"])
    res.evidence_ids = list(dict.fromkeys(res.evidence_ids))
    res.duration_ms = int((time.monotonic() - t0) * 1000)
    log.info("live lookup: %s", json.dumps(res.to_log(), ensure_ascii=False))
    return res


def live_shortlist(
    conn: Connection, embedder: Embedder | None, queries: list[str], evidence_ids: list[int],
    local: list[Hit], size: int = 8, live_max: int = LIVE_IN_SHORTLIST, per_record: int = 2,
) -> list[Hit]:
    """Live passages ranked by similarity to the claim (at most `per_record` per record), then the
    first-pass local hits to fill the shortlist."""
    rows = conn.execute(query("passages.searchable_by_evidence"), {"ids": evidence_ids}).fetchall() \
        if evidence_ids else []
    order = {eid: i for i, eid in enumerate(evidence_ids)}
    scores = np.array([-order.get(r["evidence_id"], 0) for r in rows], dtype=np.float32)
    usable = embedder is not None and rows and all(
        r["embedding"] is not None and r["embedding_model"] == embedder.name for r in rows)
    if usable and queries:
        assert embedder is not None
        m = np.stack([np.frombuffer(r["embedding"], dtype=np.float32) for r in rows])
        qv = np.stack([embedder.embed_query(q) for q in queries])
        scores = (m @ qv.T).max(axis=1)
    picked: list[tuple[int, int, float]] = []
    per: Counter[int] = Counter()
    for i in np.argsort(-scores, kind="stable"):
        r = rows[int(i)]
        if per[r["evidence_id"]] >= per_record:
            continue
        per[r["evidence_id"]] += 1
        picked.append((r["passage_id"], r["evidence_id"], float(scores[int(i)])))
        if len(picked) >= live_max:
            break
    meta = passage_meta(conn, [p for p, _, _ in picked])
    hits = [Hit(p, e, s, {"live": n}, meta[p]) for n, (p, e, s) in enumerate(picked, 1) if p in meta]
    have = {h.passage_id for h in hits}
    for h in local:
        if len(hits) >= size:
            break
        if h.passage_id not in have:
            hits.append(h)
            have.add(h.passage_id)
    return hits


# ---------------------------------------------------------------------------------------
# Verifier hook
# ---------------------------------------------------------------------------------------


def needs_live(claim: Claim, result: ClaimResult) -> bool:
    """The trigger rule (see the module docstring)."""
    return claim.checkable and result.verdict == "insufficient"


def live_pass(verifier: Verifier, claims: list[Claim], results: list[ClaimResult],
              now: datetime, **lookup_kw: Any) -> list[ClaimResult]:
    """Second pass for claims the corpus could not settle: one live lookup per check (claims of
    one message are usually about one topic, and a lookup per claim would multiply the 3 s host
    spacing), then a re-check of each such claim on the live evidence. A failed re-check keeps
    the first-pass answer."""
    todo = [i for i, (c, r) in enumerate(zip(claims, results)) if needs_live(c, r)][:MAX_LIVE_CLAIMS]
    if not todo:
        return results
    conn, embedder = verifier.retriever.conn, verifier.retriever.embedder
    out = list(results)
    lead = claims[todo[0]]
    variants = [*lead.search_queries, *(claims[i].search_queries[0] for i in todo[1:] if claims[i].search_queries)]
    try:
        lk = live_lookup(conn, embedder, lead.text, variants, **lookup_kw)
    except Exception as e:  # noqa: BLE001 - e.g. a database error while storing; keep the first answers
        log.warning("live lookup failed: %r", e)
        for i in todo:
            out[i].live = {"error": _err(e)}
        return out
    rechecks: dict[int, list[Hit]] = {}
    if lk.evidence_ids:
        for i in todo:
            c = claims[i]
            local_ids = [int(p[1:]) for p in results[i].shortlist_ids if p[1:].isdigit()]
            meta = passage_meta(conn, local_ids)
            local = [Hit(p, meta[p]["evidence_id"], 0.0, {}, meta[p]) for p in local_ids if p in meta]
            queries = list(dict.fromkeys([c.text, *c.search_queries]))
            rechecks[i] = live_shortlist(conn, embedder, queries, lk.evidence_ids, local, size=verifier.shortlist)
        with ThreadPoolExecutor(max_workers=max(1, min(verifier.workers, len(rechecks)))) as pool:
            redone = dict(zip(rechecks, pool.map(
                lambda i: verifier._check_isolated(claims[i], rechecks[i], now), list(rechecks))))
        for i, r in redone.items():
            if r.verdict != "error":
                out[i] = r
    note, logged = lk.note(), lk.to_log()
    for i in todo:
        if note:
            out[i].notes.append(note)
        out[i].live = logged
    return out
