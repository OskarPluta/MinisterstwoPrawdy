"""Adaptery źródeł: rss, html_list, sitemap, json_api, static_page."""
from __future__ import annotations

import json
import logging
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import timedelta
from typing import Any, Callable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .extract import (SKIP_EXT, ListItem, extract_article, find_attachments, find_dated_links, parse_any_date,
                      parse_feed, parse_sitemap, to_iso)
from .fetcher import Blocked, Fetcher, RobotsBlocked, RobotsUnavailable, Throttled, host_of
from .storage import Store, iso, now_utc, parse_iso, sha256

log = logging.getLogger(__name__)

MIN_CONTENT_CHARS = 80   # krótszy tekst = ekstrakcja się nie udała
MAX_PENDING = 300        # limit zaległych adresów odkładanych na kolejne przebiegi
MAX_FAILS = 3            # po tylu błędach z rzędu adres jest oznaczany jako failed


class SourceBlocked(Exception):
    """Całe źródło niedostępne (robots.txt blokuje kanał/listę, wyzwanie antybotowe, 403)."""


class SkipItem(Exception):
    """Pomijamy pojedynczy wpis (np. nie-HTML, brak opisu w kanale) – to nie błąd."""


@dataclass
class Stats:
    """Liczniki i komunikaty jednego przebiegu źródła (trafiają do crawl_log.jsonl)."""
    found: int = 0
    new: int = 0
    updated: int = 0
    unchanged: int = 0
    skipped: int = 0
    errors: int = 0
    status: str | None = None
    hint: str | None = None
    messages: list[str] = field(default_factory=list)
    blocked_urls: list[str] = field(default_factory=list)

    def count(self, outcome: str) -> None:
        setattr(self, outcome, getattr(self, outcome) + 1)

    def msg(self, m: str) -> None:
        if m not in self.messages:
            self.messages.append(m)


@dataclass
class Ctx:
    """Wspólne obiekty przebiegu przekazywane adapterom."""
    store: Store
    fetcher: Fetcher
    max_items_override: int | None = None


def base_domain(host: str) -> str:
    """Przybliżona domena rejestrowalna: policja.gov.pl, policja.pl, gov.pl/… (bez listy PSL)."""
    parts = host.lower().split(".")
    if len(parts) >= 3 and parts[-1] == "pl" and parts[-2] in {"gov", "com", "org", "net", "edu", "waw", "info", "biz"}:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


# ---------------- zapis dokumentu ----------------
def save_doc(ctx: Ctx, src: dict, url: str, title: str | None, content: str, published: str | None,
             method: str) -> str:
    """Zapisuje dokument; zwraca new | updated | unchanged."""
    extra = {}
    if src.get("attribution"):
        extra["attribution"] = src["attribution"]
    return ctx.store.save_document(
        url=url, source=src["name"], institution=src["institution"], doc_type=src.get("doc_type"),
        region=src.get("region"), title=title, content=content, published_at=published,
        fetch_method=method, extra=extra or None)


def fetch_article(ctx: Ctx, src: dict, item: ListItem, prefer_list_title: bool,
                  allow_guessed_date: bool = True) -> str:
    """Pobiera (renderuje) artykuł, wyciąga treść i zapisuje; zwraca new | updated | unchanged."""
    url = item.url
    resp = ctx.fetcher.render(url, conditional=url in ctx.store.current)
    if resp.not_modified:
        return "unchanged"
    text, title, date, method = "", None, None, resp.method
    if resp.is_html:
        ext = extract_article(resp.text, resp.final_url, content_selector=src.get("content_selector"),
                              title_selector=src.get("title_selector"),
                              strip_title_suffix=src.get("strip_title_suffix"),
                              drop_selectors=src.get("drop_selectors"))
        text, title, date = ext.text, ext.title, ext.date
        if ext.date_guessed and not allow_guessed_date:
            date = None
        log.debug("%s: ekstrakcja %s, %d znaków", url, ext.method, len(text))
    # krótka treść + załączniki: komunikat jest właściwie w pliku (np. list w PDF) – zapisz, że istnieje i gdzie
    attachments = find_attachments(resp.text, resp.final_url) if resp.is_html and len(text) < 400 else []
    if len(text) < MIN_CONTENT_CHARS and item.summary and len(item.summary) >= 40:
        text, method = item.summary, "http"  # RSS: zostaje opis z kanału
    if attachments:
        lines = [f"- {name}: {att_url}" for name, att_url in attachments[:10]]
        text = ((text + "\n\n") if text else "") + "Komunikat opublikowany w załączniku:\n" + "\n".join(lines)
    if len(text) < MIN_CONTENT_CHARS:
        if not resp.is_html:
            raise SkipItem(f"nie-HTML ({resp.content_type}): {url}")
        raise ValueError(f"nie udało się wyciągnąć treści ({len(text)} znaków): {url}")
    if prefer_list_title:
        title = item.title or title
    else:
        title = title or item.title
    published = to_iso(item.published or date)
    outcome = save_doc(ctx, src, url, title, text, published, method)
    ctx.fetcher.commit_validators(resp)
    return outcome


# ---------------- wybór kandydatów + przetwarzanie ----------------
def _item_to_dict(it: ListItem) -> dict:
    d = asdict(it)
    d["published"] = to_iso(it.published)
    d["lastmod"] = to_iso(it.lastmod)
    return d


def _item_from_dict(d: dict) -> ListItem:
    return ListItem(url=d["url"], title=d.get("title"), published=parse_iso(d.get("published")),
                    lastmod=parse_iso(d.get("lastmod")), summary=d.get("summary"))


def _sort_key_newest(it: ListItem) -> float:
    dt = it.published or it.lastmod
    return -dt.timestamp() if dt else float("inf")


def recheck_due(src: dict) -> Callable[[ListItem, dict], bool]:
    """Kryterium ponownego sprawdzenia znanego adresu: minęło `recheck_hours` od ostatniego."""
    hours = float(src.get("recheck_hours", 24))

    def due(it: ListItem, k: dict) -> bool:
        if k.get("failed"):
            return False
        checked = parse_iso(k.get("checked_at"))
        return checked is None or now_utc() - checked >= timedelta(hours=hours)
    return due


def select_candidates(ctx: Ctx, src: dict, st: dict, items: list[ListItem],
                      is_due: Callable[[ListItem, dict], bool]) -> list[ListItem]:
    """Wybiera do `limit` adresów: najpierw nowe (najświeższe), potem do ponownego sprawdzenia
    (najdawniej sprawdzane). Nadmiarowe nowe odkłada do st["pending"]."""
    known: dict = st["known"]
    limit = ctx.max_items_override if ctx.max_items_override is not None else int(src.get("max_items_per_run", 30))
    new: list[ListItem] = []
    due: list[ListItem] = []
    seen: set[str] = set()
    for it in items:
        if it.url in seen:
            continue
        seen.add(it.url)
        k = known.get(it.url)
        if k is None or (k.get("fails", 0) and not k.get("failed")):
            new.append(it)
        elif is_due(it, k):
            due.append(it)
    for d in st.get("pending", []):  # zaległe z poprzednich przebiegów (np. spadły z listy)
        if d["url"] not in seen and d["url"] not in known:
            seen.add(d["url"])
            new.append(_item_from_dict(d))
    new.sort(key=_sort_key_newest)
    due.sort(key=lambda it: known[it.url].get("checked_at") or "")
    cands = new[:limit] + due[:max(0, limit - len(new))]
    st["pending"] = [_item_to_dict(i) for i in new[limit:]][:MAX_PENDING]
    return cands


def _requeue(st: dict, it: ListItem) -> None:
    """Odkłada nieprzetworzony nowy adres na początek kolejki zaległych."""
    if it.url not in st["known"]:
        st["pending"] = ([_item_to_dict(it)] + st.get("pending", []))[:MAX_PENDING]


def _mark_known(st: dict, it: ListItem, **extra) -> None:
    k = st["known"].setdefault(it.url, {"first_seen": iso(now_utc())})
    k["checked_at"] = iso(now_utc())
    if it.lastmod:
        k["lastmod"] = to_iso(it.lastmod)
    if it.published:
        k["published"] = to_iso(it.published)
    k.pop("fails", None)
    k.pop("failed", None)
    k.update(extra)


def process_items(ctx: Ctx, src: dict, st: dict, stats: Stats, cands: list[ListItem],
                  handler: Callable[[ListItem], str]) -> None:
    """Przetwarza kandydatów; blokada/wycofanie hosta wstrzymuje tylko ten host. Jeśli stoją
    wszystkie hosty źródła – ponownie rzuca wyjątek (status źródła blocked/throttled)."""
    dead: dict[str, Exception] = {}  # host → powód (blokada / wycofanie); innych hostów to nie dotyczy
    for it in cands:
        host = host_of(it.url)
        if host in dead:
            _requeue(st, it)
            continue
        try:
            outcome = handler(it)
            stats.count(outcome)
            _mark_known(st, it)
        except RobotsBlocked:
            stats.skipped += 1
            stats.blocked_urls.append(it.url)
            log.warning("[%s] pomijam (robots.txt): %s", src["name"], it.url)
            _mark_known(st, it, robots_blocked=True)
        except SkipItem as e:
            stats.skipped += 1
            log.info("[%s] pomijam: %s", src["name"], e)
            _mark_known(st, it, skipped=str(e)[:200])
        except (Throttled, Blocked, RobotsUnavailable) as e:
            # nie męczymy dalej tego hosta; nieprzetworzone nowe adresy zostają na następny przebieg
            dead[host] = e
            stats.msg(str(e))
            _requeue(st, it)
        except Exception as e:
            stats.errors += 1
            stats.msg(f"{type(e).__name__}: {str(e)[:200]}")
            log.error("[%s] błąd dla %s: %s: %s", src["name"], it.url, type(e).__name__, e)
            k = st["known"].get(it.url)
            if k is None:
                k = st["known"][it.url] = {"first_seen": iso(now_utc()), "fails": 0}
            k["fails"] = k.get("fails", 0) + 1
            if k["fails"] >= MAX_FAILS:
                k["failed"] = True  # nie próbuj w nieskończoność
                k["checked_at"] = iso(now_utc())
    if dead:
        all_hosts = {host_of(c.url) for c in cands}
        if set(dead) >= all_hosts:
            raise next(iter(dead.values()))  # cały wolumen źródła stoi → status blocked/throttled
        stats.status = "warn"
        stats.msg("część hostów niedostępna: " + ", ".join(sorted(dead)))


# ---------------- adaptery ----------------
def run_rss(ctx: Ctx, src: dict, st: dict, stats: Stats) -> None:
    """Kanały RSS/Atom; wpisy spoza domeny kanału są pomijane."""
    items: list[ListItem] = []
    resps = []
    blocked = 0
    not_modified = 0
    for feed_url in src["urls"]:
        try:
            resp = ctx.fetcher.http_get(feed_url, accept="application/rss+xml, application/atom+xml, "
                                                         "application/xml;q=0.9, text/xml;q=0.9, */*;q=0.5")
        except RobotsBlocked:
            blocked += 1
            stats.blocked_urls.append(feed_url)
            log.warning("[%s] kanał zablokowany w robots.txt: %s", src["name"], feed_url)
            continue
        if resp.not_modified:
            # kanał bez zmian – ale wpisy wciąż „są na liście”, więc recheck_hours nadal działa
            not_modified += 1
            items += [_item_from_dict(d) for d in st.setdefault("list_cache", {}).get(feed_url, [])]
            continue
        try:
            parsed = parse_feed(resp.content, resp.final_url)
        except Exception as e:
            stats.errors += 1
            stats.msg(f"kanał {feed_url}: nie da się sparsować ({e})")
            continue
        if not parsed:
            stats.msg(f"kanał {feed_url} jest pusty")
        feed_base = base_domain(host_of(resp.final_url))
        kept = []
        for it in parsed:
            if base_domain(host_of(it.url)) != feed_base:
                stats.msg(f"pominięto link spoza domeny kanału: {host_of(it.url)}")
                continue
            kept.append(it)
        items += kept
        st.setdefault("list_cache", {})[feed_url] = [_item_to_dict(i) for i in kept]
        resps.append(resp)
    if blocked == len(src["urls"]):
        raise SourceBlocked("robots.txt blokuje wszystkie kanały – nic nie pobieram")
    if blocked:
        stats.msg(f"{blocked} kanał(y) zablokowane w robots.txt")
    stats.found = len({i.url for i in items})
    cands = select_candidates(ctx, src, st, items, recheck_due(src))
    if not_modified + blocked == len(src["urls"]):
        stats.hint = "not_modified"
    fetch_full = src.get("fetch_full", True)

    def handler(it: ListItem) -> str:
        if fetch_full:
            return fetch_article(ctx, src, it, prefer_list_title=True)
        ctx.fetcher.check_allowed(it.url)  # nawet bez pobierania nie zapisujemy zablokowanych adresów
        if not it.summary:
            raise SkipItem(f"brak opisu w kanale: {it.url}")
        return save_doc(ctx, src, it.url, it.title, it.summary, to_iso(it.published), "http")

    process_items(ctx, src, st, stats, cands, handler)
    for r in resps:
        ctx.fetcher.commit_validators(r)


def _page_url(url: str, template: str, n: int) -> str:
    """template np. 'page={n}&size=10' – parametry dopisywane/podmieniane w query."""
    p = urlsplit(url)
    q = dict(parse_qsl(p.query))
    q.update(dict(parse_qsl(template.format(n=n).lstrip("?"))))
    return urlunsplit((p.scheme, p.netloc, p.path, urlencode(q), ""))


def run_html_list(ctx: Ctx, src: dict, st: dict, stats: Stats) -> None:
    """Listy aktualności (np. gov.pl) z opcjonalnym stronicowaniem."""
    items: list[ListItem] = []
    resps = []
    fetched_pages = 0
    not_modified = 0
    pag = src.get("pagination") or {}
    for list_url in src["urls"]:
        pages = [list_url]
        if pag.get("template"):
            start = int(pag.get("start", 2))
            pages += [_page_url(list_url, pag["template"], n)
                      for n in range(start, start + int(pag.get("max_pages", 1)) - 1)]
        for page_url in pages:
            resp = ctx.fetcher.render(page_url, conditional=page_url == list_url)
            if resp.not_modified:
                not_modified += 1
                items += [_item_from_dict(d) for d in st.setdefault("list_cache", {}).get(page_url, [])]
                break
            fetched_pages += 1
            links = find_dated_links(resp.text, resp.final_url, path_prefix=src.get("path_prefix"),
                                     link_selector=src.get("link_selector"),
                                     exclude_regex=src.get("exclude_regex"),
                                     require_date=src.get("require_date"),
                                     skip_chrome=src.get("skip_chrome"))
            items += links
            if page_url == list_url:
                st.setdefault("list_cache", {})[page_url] = [_item_to_dict(i) for i in links]
            resps.append(resp)
            # kolejną stronę pobieramy tylko, gdy cała bieżąca była nowa
            if not links or any(l.url in st["known"] for l in links):
                break
    stats.found = len({i.url for i in items})
    if fetched_pages and not items:
        stats.status = "warn"
        stats.msg("0 znalezionych linków – prawdopodobnie zmieniła się struktura strony")
    cands = select_candidates(ctx, src, st, items, recheck_due(src))
    if not fetched_pages and not_modified:
        stats.hint = "not_modified"
    process_items(ctx, src, st, stats, cands, lambda it: fetch_article(ctx, src, it, prefer_list_title=False))
    for r in resps:
        ctx.fetcher.commit_validators(r)


def run_sitemap(ctx: Ctx, src: dict, st: dict, stats: Stats) -> None:
    """Sitemapy (także sitemapindex) – tylko adresy z hosta pierwszej sitemapy."""
    root = src["urls"][0]
    host = host_of(root)
    excl = re.compile(src["exclude_regex"]) if src.get("exclude_regex") else None
    prefix = src.get("path_prefix")
    max_sub = int(src.get("max_sitemaps", 20))
    resps = []
    entries: list[ListItem] = []
    queue = [root]
    visited = 0
    while queue and visited < max_sub:
        sm_url = queue.pop(0)
        visited += 1
        resp = ctx.fetcher.http_get(sm_url, accept="application/xml, text/xml;q=0.9, */*;q=0.5")
        if resp.not_modified:
            continue
        urls, subs = parse_sitemap(resp.content)
        resps.append(resp)
        subs = [s for s in subs if host_of(s.url) == host]
        subs.sort(key=_sort_key_newest)
        queue += [s.url for s in subs]
        entries += urls
    if not resps and visited:
        stats.hint = "not_modified"
    items = []
    foreign = 0
    for it in entries:
        p = urlsplit(it.url)
        if (p.hostname or "").lower() != host:
            foreign += 1
            continue
        if SKIP_EXT.search(p.path) or (prefix and not p.path.startswith(prefix)) or (excl and excl.search(it.url)):
            continue
        items.append(it)
    if foreign:
        stats.msg(f"zignorowano {foreign} adres(ów) z obcej domeny")
    stats.found = len(items)
    if resps and not items:
        stats.status = "warn"
        stats.msg("sitemapa nie zawiera adresów z tej domeny")
    time_due = recheck_due(src)

    def is_due(it: ListItem, k: dict) -> bool:
        # z lastmod: sprawdzamy ponownie tylko, gdy się zmienił; bez lastmod – co recheck_hours
        if k.get("failed"):
            return False
        if it.lastmod is not None:
            prev = parse_iso(k.get("lastmod"))
            return prev is None or it.lastmod > prev
        return time_due(it, k)

    # sitemapy: najświeższe najpierw (lastmod jako proxy daty)
    cands = select_candidates(ctx, src, st, items, is_due)
    for it in cands:
        it.published = None  # lastmod ≠ data publikacji; datę bierzemy z artykułu
    process_items(ctx, src, st, stats, cands, lambda it: fetch_article(ctx, src, it, prefer_list_title=False))
    for r in resps:
        ctx.fetcher.commit_validators(r)


def _flatten(obj: Any, prefix: str = "", sort_lists: bool = False) -> list[tuple[str, str]]:
    """JSON → [(klucz, wartość)], np. obszary[0].wojewodztwo; listy skalarów łączone przecinkami."""
    out: list[tuple[str, str]] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            out += _flatten(v, f"{prefix}.{k}" if prefix else str(k), sort_lists)
    elif isinstance(obj, list):
        if all(not isinstance(x, (dict, list)) for x in obj):
            if obj:
                vals = [str(x) for x in obj]
                out.append((prefix, ", ".join(sorted(vals) if sort_lists else vals)))
        else:
            for i, v in enumerate(obj):
                out += _flatten(v, f"{prefix}[{i}]", sort_lists)
    elif obj is not None and str(obj).strip() != "":
        out.append((prefix, str(obj).strip()))
    return out


_FIELD_RE = re.compile(r"\{([^{}:]+)(?::([^{}]*))?\}")


def render_template(template: str, flat: dict[str, str]) -> str:
    """{klucz} / {klucz:.4} – klucz to dosłownie nazwa po spłaszczeniu, np. {obszary[0].wojewodztwo}.
    Brakujące pola dają pusty napis."""
    def sub(m: re.Match) -> str:
        val = flat.get(m.group(1).strip(), "")
        return format(val, m.group(2)) if m.group(2) else val
    return _FIELD_RE.sub(sub, template)


def _slug(s: str) -> str:
    """ASCII-owy identyfikator z małych liter, cyfr i myślników."""
    s = unicodedata.normalize("NFKD", s.replace("ł", "l").replace("Ł", "L"))
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def _dig(obj: Any, path: str | None) -> Any:
    """Wartość pod ścieżką „a.0.b” (liczby indeksują listy); pusta ścieżka = cały obiekt."""
    if not path:
        return obj
    for part in path.split("."):
        obj = obj[int(part)] if isinstance(obj, list) else obj[part]
    return obj


def run_json_api(ctx: Ctx, src: dict, st: dict, stats: Stats) -> None:
    """API JSON: każdy rekord to dokument o adresie endpoint#id (treść = spłaszczone pola)."""
    endpoint = src["urls"][0]
    resp = ctx.fetcher.http_get(endpoint, accept="application/json")
    if resp.not_modified:
        stats.hint = "not_modified"
        return
    data = json.loads(resp.text)
    records = _dig(data, src.get("records_path"))
    if isinstance(records, dict):
        lists = [v for v in records.values() if isinstance(v, list)]
        records = lists[0] if lists else [records]
    if not isinstance(records, list):
        raise ValueError(f"Odpowiedź API nie zawiera listy rekordów (typ {type(records).__name__})")
    stats.found = len(records)
    id_field = src.get("id_field")
    id_template = src.get("id_template")  # np. "{numer}-{opublikowano:.4}-{biuro}" gdy API nie ma pola id
    template = src.get("title_template")
    date_fields = src.get("date_fields") or []
    attribution = src.get("attribution")
    if not id_field and not id_template:
        stats.msg("brak id_field – identyfikator = hash rekordu (zmiana treści da nowy dokument zamiast wersji)")
    seen_ids = []
    for rec in records:
        try:
            flat = _flatten(rec, sort_lists=bool(src.get("sort_lists")))
            if id_template:
                rid = _slug(render_template(id_template, dict(flat)))
                if not rid:
                    raise ValueError("id_template dał pusty identyfikator")
            elif id_field:
                rid = _dig(rec, id_field)
                if rid in (None, ""):
                    raise ValueError(f"rekord bez pola {id_field}")
                rid = str(rid)
            else:
                rid = sha256(json.dumps(rec, sort_keys=True, ensure_ascii=False))[:16]
            if rid in seen_ids:  # ten sam id dwa razy w odpowiedzi – bez sufiksu wersje by się przeplatały
                n = sum(1 for s in seen_ids if s == rid or s.startswith(rid + "~")) + 1
                stats.msg(f"powtórzony identyfikator {rid!r} w odpowiedzi – zapisany jako {rid}~{n}")
                rid = f"{rid}~{n}"
            seen_ids.append(rid)
            url = f"{endpoint}#{rid}"
            content = "\n".join(f"{k}: {v}" for k, v in flat)
            if attribution:
                content += f"\n\n{attribution}"
            fd = dict(flat)
            title = re.sub(r"\s+", " ", render_template(template, fd)).strip(" –-") if template else None
            title = re.sub(r"\(\s*\)", "", title or "").strip() or None
            published = None
            for f in date_fields:
                v = fd.get(f)
                if v and (dt := parse_any_date(v)):
                    published = to_iso(dt)
                    break
            stats.count(save_doc(ctx, src, url, title, content, published, "http"))
        except Exception as e:
            stats.errors += 1
            stats.msg(f"{type(e).__name__}: {str(e)[:200]}")
            log.error("[%s] błąd rekordu: %s", src["name"], e)
    st["active_ids"] = seen_ids  # aktualnie obowiązujące rekordy (np. ostrzeżenia)
    ctx.fetcher.commit_validators(resp)


def run_static_page(ctx: Ctx, src: dict, st: dict, stats: Stats) -> None:
    """Pojedyncze strony obserwowane pod kątem zmian treści."""
    items = [ListItem(url=u) for u in src["urls"]]
    stats.found = len(items)
    process_items(ctx, src, st, stats, items,
                  lambda it: fetch_article(ctx, src, it, prefer_list_title=False, allow_guessed_date=False))
    if stats.unchanged == len(items) and not stats.errors:
        stats.hint = "not_modified"


ADAPTERS = {
    "rss": run_rss,
    "html_list": run_html_list,
    "sitemap": run_sitemap,
    "json_api": run_json_api,
    "static_page": run_static_page,
}
