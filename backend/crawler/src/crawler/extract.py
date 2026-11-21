"""Ekstrakcja: daty, tekst artykułu (selektor → trafilatura → <article>/<main>), linki z datą, RSS/Atom, sitemapy."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup, Tag
from lxml import etree

log = logging.getLogger(__name__)
WARSAW = ZoneInfo("Europe/Warsaw")

# ---------------- daty ----------------
MONTHS = {
    "stycznia": 1, "lutego": 2, "marca": 3, "kwietnia": 4, "maja": 5, "czerwca": 6, "lipca": 7,
    "sierpnia": 8, "września": 9, "wrzesnia": 9, "października": 10, "pazdziernika": 10,
    "listopada": 11, "grudnia": 12,
}
_TIME = r"(?:\s*(?:r\.)?\s*,?\s*(?:godz\.?|o\s+godz\.?)?\s*(\d{1,2})[:.](\d{2}))?"
DATE_PATTERNS = [
    re.compile(r"(?<!\d)(\d{1,2})\.(\d{1,2})\.(\d{4})(?!\d)" + _TIME),             # 21.11.2026 14:05
    re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}))?"),          # 2026-11-21
    re.compile(r"(?<!\d)(\d{1,2})\s+(" + "|".join(MONTHS) + r")\s+(\d{4})" + _TIME, re.I),  # 21 listopada 2026
    re.compile(r"(?<![\d.-])(\d{1,2})-(\d{1,2})-(\d{4})(?![\d-])" + _TIME),                 # 21-11-2026
]


def find_date(text: str) -> datetime | None:
    """Pierwsza (najwcześniej w tekście) data w jednym z obsługiwanych formatów, strefa Europe/Warsaw."""
    best: tuple[int, datetime] | None = None
    for i, rx in enumerate(DATE_PATTERNS):
        for m in rx.finditer(text):
            try:
                g = m.groups()
                if i in (0, 3):
                    d, mo, y, hh, mm = g
                elif i == 1:
                    y, mo, d, hh, mm = g
                else:
                    d, mon, y, hh, mm = g
                    mo = MONTHS[mon.lower()]
                dt = datetime(int(y), int(mo), int(d), int(hh or 0), int(mm or 0), tzinfo=WARSAW)
            except (ValueError, KeyError):
                continue
            if not 1990 <= dt.year <= 2100:
                continue
            if best is None or m.start() < best[0]:
                best = (m.start(), dt)
            break
    return best[1] if best else None


def parse_any_date(value: str | None) -> datetime | None:
    """Daty z RSS (RFC 822), ISO 8601 (sitemap, Atom, API) albo formaty polskie."""
    if not value:
        return None
    value = value.strip()
    try:
        dt = parsedate_to_datetime(value)
        if dt:
            return dt if dt.tzinfo else dt.replace(tzinfo=WARSAW)
    except (TypeError, ValueError, IndexError):
        pass
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=WARSAW)
    except ValueError:
        pass
    if re.fullmatch(r"\d{14}", value):  # YYYYMMDDHHMMSS (np. API Bałtyku IMGW)
        try:
            return datetime.strptime(value, "%Y%m%d%H%M%S").replace(tzinfo=WARSAW)
        except ValueError:
            pass
    return find_date(value)


def to_iso(dt: datetime | None) -> str | None:
    """datetime → ISO 8601 (None przechodzi bez zmian)."""
    return dt.isoformat() if dt else None


# ---------------- tekst artykułu ----------------
_WS = re.compile(r"[ \t\u00a0]+")  # spacje, tabulatory i twarde spacje (&nbsp;)
_NL = re.compile(r"\n{3,}")


def clean_text(text: str) -> str:
    """Zwija białe znaki w liniach i nadmiarowe puste linie (maks. jedna pusta z rzędu)."""
    lines = [_WS.sub(" ", ln).strip() for ln in text.splitlines()]
    return _NL.sub("\n\n", "\n".join(lines)).strip()


_COOKIE_ATTR = re.compile(r"cookie|consent|gdpr|rodo-|cmplz|onetrust|cookiebar", re.I)
_COOKIE_TEXT = re.compile(r"(plik(i|ów)? cookie|cookies|ciasteczk)", re.I)
_ATTACH_EXT = re.compile(r"\.(pdf|docx?|odt|rtf|xlsx?|ods)(\?|$)", re.I)


def _drop_cookie_banners(soup: BeautifulSoup) -> None:
    """Usuwa banery cookies/RODO (po id/klasie albo krótkie bloki o cookies), zanim cokolwiek wyciągniemy."""
    for el in soup.find_all(True):
        if getattr(el, "decomposed", False):  # potomek usuniętego już wcześniej banera
            continue
        attrs = " ".join([el.get("id") or ""] + list(el.get("class") or []))
        if attrs and _COOKIE_ATTR.search(attrs) and el.name not in ("html", "body", "main", "article"):
            if len(el.get_text(" ", strip=True)) < 2000:
                el.decompose()
    for el in soup.find_all(["div", "section", "aside", "p"]):
        t = el.get_text(" ", strip=True)
        if 0 < len(t) < 700 and _COOKIE_TEXT.search(t) and not el.find(["article", "main"]) \
                and re.search(r"(zgod|akceptuj|ustawie(nia|ń) przeglądarki|wyrażasz|zgadzasz)", t, re.I):
            el.decompose()


def find_attachments(html: str | bytes, base_url: str) -> list[tuple[str, str]]:
    """Linki do załączników (PDF/DOC…) w treści strony: [(nazwa, url)]."""
    soup = BeautifulSoup(html, "lxml")
    scope = soup.select_one("article") or soup.select_one("main") or soup
    out, seen = [], set()
    for a in scope.find_all("a", href=True):
        url = urljoin(base_url, a["href"])
        if (_ATTACH_EXT.search(urlsplit(url).path) or "/download/" in url or "/attachment/" in url) and url not in seen:
            seen.add(url)
            out.append((a.get_text(" ", strip=True) or url.rsplit("/", 1)[-1], url))
    return out


@dataclass
class Extracted:
    """Wynik ekstrakcji artykułu; `method`: selector | trafilatura | bs4 | none."""
    title: str | None
    text: str
    date: datetime | None
    method: str
    date_guessed: bool = False   # data z heurystyki trafilatury (a nie z metadanych strony)


_DROP_TAGS = ["script", "style", "noscript", "nav", "header", "footer", "aside", "form", "iframe",
              "svg", "button", "template"]


_BLOCK_TAGS = ["p", "div", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "table", "section",
               "article", "blockquote", "dt", "dd", "dl", "pre", "figcaption", "header", "address"]


def _soup_text(node: Tag) -> str:
    """Tekst z nowymi liniami tylko na granicach bloków (linki/pogrubienia zostają w zdaniu)."""
    for t in node.find_all(_DROP_TAGS):
        t.decompose()
    for br in node.find_all("br"):
        br.replace_with("\n")
    for t in node.find_all(_BLOCK_TAGS):
        t.insert_before("\n")
        t.append("\n")
    for cell in node.find_all(["td", "th"]):
        cell.append(" | ")
    return clean_text(node.get_text(""))


def _meta(soup: BeautifulSoup, *names: str) -> str | None:
    """Treść pierwszego znalezionego <meta property|name=…>."""
    for n in names:
        tag = soup.find("meta", attrs={"property": n}) or soup.find("meta", attrs={"name": n})
        if tag and tag.get("content"):
            return tag["content"].strip()
    return None


def extract_article(html: str | bytes, url: str, content_selector: str | None = None,
                    title_selector: str | None = None, strip_title_suffix: str | None = None,
                    drop_selectors: list[str] | None = None) -> Extracted:
    """Tytuł, tekst i data artykułu. Treść: `content_selector` → trafilatura → <article>/<main>
    (kolejny sposób tylko, gdy poprzedni dał < 80 znaków)."""
    import trafilatura

    soup = BeautifulSoup(html, "lxml")
    _drop_cookie_banners(soup)
    if drop_selectors:
        for sel in drop_selectors:
            for t in soup.select(sel):
                t.decompose()
    html_str = str(soup)

    # tytuł: selektor z konfiguracji > <h1> w treści > og:title > <title>
    title = None
    if title_selector and (t := soup.select_one(title_selector)):
        title = t.get_text(" ", strip=True)
    if not title:
        scope = soup.select_one("article") or soup.select_one("main") or soup
        h1 = scope.find("h1") or soup.find("h1")
        if h1 and h1.get_text(strip=True):
            title = h1.get_text(" ", strip=True)
    if not title:
        title = _meta(soup, "og:title") or (soup.title.get_text(" ", strip=True) if soup.title else None)
    if title and strip_title_suffix:
        title = re.sub(strip_title_suffix + r"\s*$", "", title).strip()

    date = parse_any_date(_meta(soup, "article:published_time", "datePublished", "dc.date", "DC.date.issued"))
    date_guessed = False

    text, method = "", ""
    if content_selector:
        nodes = soup.select(content_selector)  # wszystkie pasujące bloki, w kolejności w dokumencie
        if nodes:
            text, method = clean_text("\n\n".join(_soup_text(n) for n in nodes)), "selector"
    if len(text) < 80:
        t = trafilatura.extract(html_str, url=url, include_comments=False, include_tables=True,
                                favor_precision=True, deduplicate=True)
        if t and len(t.strip()) >= 80:
            text, method = clean_text(t), "trafilatura"
        if date is None:
            try:
                md = trafilatura.extract_metadata(html_str, default_url=url)
                if md and md.date:
                    date = parse_any_date(md.date)
                    date_guessed = date is not None
            except Exception:
                pass
    if len(text) < 80:
        node = soup.select_one("article") or soup.select_one("main") or soup.select_one("[role=main]")
        if node is not None:
            t = _soup_text(node)
            if len(t) > len(text):
                text, method = t, "bs4"
    return Extracted(title=title or None, text=text, date=date, method=method or "none", date_guessed=date_guessed)


# ---------------- linki z listy aktualności ----------------
SKIP_EXT = re.compile(
    r"\.(pdf|docx?|xlsx?|pptx?|odt|ods|odp|rtf|zip|rar|7z|gz|jpe?g|png|gif|bmp|svg|webp|tiff?|mp3|mp4|avi|mov|wmv|xml|csv)$",
    re.I)
DEFAULT_EXCLUDE = re.compile(r"/attachment/|/(?:download|pobierz)/|[?&](?:print|format)=", re.I)


@dataclass
class ListItem:
    """Wpis z listy / kanału / sitemapy – kandydat do pobrania."""
    url: str
    title: str | None = None
    published: datetime | None = None
    lastmod: datetime | None = None
    summary: str | None = None


def _norm_link(base: str, href: str | None) -> str | None:
    """Absolutny adres http(s) bez fragmentu albo None (mailto:, javascript:, kotwice…)."""
    if not href:
        return None
    href = href.strip()
    if href.startswith(("mailto:", "tel:", "javascript:", "#", "data:")):
        return None
    full = urljoin(base, href)
    p = urlsplit(full)
    if p.scheme not in ("http", "https"):
        return None
    return p._replace(fragment="").geturl()


def find_dated_links(html: str | bytes, base_url: str, *, path_prefix: str | None = None,
                     link_selector: str | None = None, exclude_regex: str | None = None,
                     require_date: bool | None = None, max_depth: int = 8,
                     skip_chrome: bool | None = None, max_tile_chars: int = 1500) -> list[ListItem]:
    """Domyślna heurystyka: link, przy którym stoi data. Datę bierzemy tylko z przodków, w których
    ten link jest JEDYNYM linkiem (kafelek artykułu) – idziemy w górę drzewa i zatrzymujemy się
    na pierwszym przodku zawierającym inny link. Dzięki temu linki z menu nie łapią dat z treści strony."""
    soup = BeautifulSoup(html, "lxml")
    base_host = (urlsplit(base_url).hostname or "").lower()
    base_norm = _norm_link(base_url, base_url)
    excl = re.compile(exclude_regex) if exclude_regex else None
    if require_date is None:
        require_date = link_selector is None
    if skip_chrome is None:
        skip_chrome = link_selector is None
    anchors = soup.select(link_selector) if link_selector else soup.find_all("a", href=True)

    out: dict[str, ListItem] = {}
    for a in anchors:
        if a.name != "a":
            a = a.find("a", href=True) or a
        url = _norm_link(base_url, a.get("href"))
        if not url or url == base_norm or url in out:
            continue
        p = urlsplit(url)
        if (p.hostname or "").lower() != base_host:
            continue
        if SKIP_EXT.search(p.path) or DEFAULT_EXCLUDE.search(url):
            continue
        if path_prefix and not p.path.startswith(path_prefix):
            continue
        if excl and excl.search(url):
            continue
        # linki w menu / nagłówku / stopce to nie artykuły (chyba że selektor mówi inaczej)
        if skip_chrome and a.find_parent(["nav", "header", "footer"]) is not None:
            continue

        date = find_date(a.get_text(" ", strip=True))
        title = a.get_text(" ", strip=True) or a.get("title") or None
        node: Tag | None = a
        depth = 0
        while date is None and depth < max_depth:
            node = node.parent if node is not None else None
            if node is None or node.name in ("body", "html", "[document]"):
                break
            links = {_norm_link(base_url, x.get("href")) for x in node.find_all("a", href=True)}
            links.discard(None)
            if len(links) > 1:
                break  # przodek ma inne linki → to już nie kafelek tego artykułu
            node_text = node.get_text(" ", strip=True)
            if len(node_text) > max_tile_chars:
                break  # kafelek jest krótki; długi blok tekstu to już treść strony
            date = find_date(node_text)
            if not title:
                h = node.find(["h1", "h2", "h3", "h4"])
                title = h.get_text(" ", strip=True) if h else None
            depth += 1
        if require_date and date is None:
            continue
        if title and date and find_date(title) and len(title) < 30:
            title = None  # tekst linku to sama data
        out[url] = ListItem(url=url, title=title, published=date)
    return list(out.values())


# ---------------- RSS / Atom ----------------
def _xml_root(content: bytes) -> etree._Element:
    """Parsuje XML bez encji zewnętrznych i dostępu do sieci (ochrona przed XXE)."""
    parser = etree.XMLParser(recover=True, resolve_entities=False, no_network=True, huge_tree=False)
    root = etree.fromstring(content.lstrip(), parser=parser)
    if root is None:
        raise ValueError("Nie udało się sparsować XML")
    return root


def _child(el: etree._Element, *names: str) -> etree._Element | None:
    """Pierwsze dziecko o jednej z nazw lokalnych (ignorując przestrzenie nazw); kolejność nazw = priorytet."""
    for n in names:
        for c in el:
            if isinstance(c.tag, str) and etree.QName(c).localname == n:
                return c
    return None


def _text(el: etree._Element | None) -> str | None:
    """Cały tekst elementu (z potomkami) albo None, gdy pusty."""
    if el is None:
        return None
    t = "".join(el.itertext()).strip()
    return t or None


def html_to_text(s: str | None) -> str | None:
    """Fragment HTML (np. opis z RSS) → czysty tekst."""
    if not s:
        return None
    if "<" not in s:
        return clean_text(s)
    return clean_text(BeautifulSoup(s, "lxml").get_text("\n"))


def parse_feed(content: bytes, base_url: str) -> list[ListItem]:
    """Wpisy RSS 2.0 / Atom (link, tytuł, data, opis)."""
    root = _xml_root(content)
    items = root.xpath("//*[local-name()='item' or local-name()='entry']")
    out: list[ListItem] = []
    for it in items:
        link = None
        for c in it:
            if not isinstance(c.tag, str) or etree.QName(c).localname != "link":
                continue
            if c.get("href") and c.get("rel", "alternate") == "alternate":
                link = c.get("href")
                break
            if (c.text or "").strip():
                link = c.text.strip()
                break
        if not link:
            g = _child(it, "guid", "id")
            if g is not None and (g.text or "").startswith("http") and g.get("isPermaLink", "true") != "false":
                link = g.text.strip()
        if not link:
            continue
        date = parse_any_date(_text(_child(it, "pubDate", "published", "date", "issued", "updated", "modified")))
        summary = _text(_child(it, "encoded")) or _text(_child(it, "content")) or \
            _text(_child(it, "description", "summary"))
        out.append(ListItem(url=urljoin(base_url, link).split("#")[0],
                            title=html_to_text(_text(_child(it, "title"))),
                            published=date, summary=html_to_text(summary)))
    return out


# ---------------- sitemap ----------------
def parse_sitemap(content: bytes) -> tuple[list[ListItem], list[ListItem]]:
    """Zwraca (adresy, podmapy) – sitemapindex daje podmapy."""
    root = _xml_root(content)
    urls, subs = [], []
    for el in root.xpath("//*[local-name()='url' or local-name()='sitemap']"):
        loc = _text(_child(el, "loc"))
        if not loc:
            continue
        item = ListItem(url=loc, lastmod=parse_any_date(_text(_child(el, "lastmod"))))
        (subs if etree.QName(el).localname == "sitemap" else urls).append(item)
    return urls, subs
