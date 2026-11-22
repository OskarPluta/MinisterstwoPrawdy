"""Pobieranie: robots.txt, limit zapytań na host, wycofanie (429/503/403), zapytania warunkowe,
renderowanie HTML w Playwright (jedna przeglądarka na proces)."""
from __future__ import annotations

import gzip
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from .config import HARD_MIN_DELAY, LOCAL_HOSTS
from .robots import RobotsRules, parse_robots
from .storage import Store, iso, now_utc, parse_iso

log = logging.getLogger(__name__)


# ---------------- wyjątki ----------------
class FetchError(Exception):
    """Bazowy wyjątek pobierania."""


class RobotsBlocked(FetchError):
    """Adres zablokowany w robots.txt – pomijamy (to nie błąd)."""


class RobotsUnavailable(FetchError):
    """robots.txt zwrócił 5xx / błąd sieci – na razie nic nie pobieramy z tego hosta."""


class Throttled(FetchError):
    """429/503 lub aktywne wycofanie dla hosta."""


class Blocked(FetchError):
    """403 albo wyzwanie antybotowe / CAPTCHA. Nie obchodzimy."""


class HttpError(FetchError):
    """Błąd sieci, status 4xx/5xx (poza obsługiwanymi wyżej) albo za dużo przekierowań."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


@dataclass
class Resp:
    """Odpowiedź po pobraniu (HTTP) lub renderowaniu (Playwright)."""
    url: str                     # adres żądany (bez fragmentu)
    final_url: str               # po przekierowaniach
    status: int
    headers: dict = field(default_factory=dict)
    content: bytes = b""
    text: str = ""
    etag: str | None = None
    last_modified: str | None = None
    method: str = "http"

    @property
    def not_modified(self) -> bool:
        return self.status == 304

    @property
    def content_type(self) -> str:
        return (self.headers.get("content-type") or "").split(";")[0].strip().lower()

    @property
    def is_html(self) -> bool:
        ct = self.content_type
        if ct in ("text/html", "application/xhtml+xml"):
            return True
        if not ct:
            head = self.content[:512].lstrip().lower()
            return head.startswith(b"<!doctype html") or head.startswith(b"<html")
        return False


# ---------------- wykrywanie wyzwań antybotowych ----------------
# Znaczniki typowe dla stron-wyzwań (Cloudflare/Incapsula/DataDome/PerimeterX)
_CHALLENGE_MARKERS = [
    "cf-browser-verification", "cf_chl_opt", "cf-challenge", "/cdn-cgi/challenge-platform/h/",
    "<title>just a moment...</title>", "attention required! | cloudflare", "checking your browser before",
    "request unsuccessful. incapsula incident id", "incapsula incident id", "_incapsula_resource?swjiylwa",
    "captcha-delivery.com", "px-captcha", "ddos protection by",
]
# Ogólne słowa – zwykłe strony też je mają (np. reCAPTCHA w formularzu komentarzy WordPressa),
# więc liczą się tylko na niemal pustej stronie albo przy statusie błędu.
_WEAK_MARKERS = ["g-recaptcha", "h-captcha", "hcaptcha.com", "recaptcha/api", "captcha", "are you a robot",
                 "nie jesteś robotem", "access denied", "dostęp zabroniony"]
_TAG_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>|<[^>]+>", re.S | re.I)


def looks_like_challenge(html: str, status: int = 200) -> str | None:
    """Zwraca znaleziony znacznik, jeśli strona wygląda na wyzwanie antybotowe / CAPTCHA."""
    low = html.lower()
    strong = next((m for m in _CHALLENGE_MARKERS if m in low), None)
    weak = None if strong else next((m for m in _WEAK_MARKERS if m in low), None)
    if not strong and not weak:
        return None
    visible = re.sub(r"\s+", " ", _TAG_RE.sub(" ", html)).strip()
    error_status = status in (403, 429, 503)
    if strong and (error_status or len(visible) < 3000):
        return strong
    if weak and (error_status or len(visible) < 400):
        return weak
    return None


# ---------------- pomocnicze ----------------
def strip_fragment(url: str) -> str:
    """Adres bez #fragmentu, z pustą ścieżką zamienioną na „/”."""
    p = urlsplit(url)
    return urlunsplit((p.scheme, p.netloc, p.path or "/", p.query, ""))


def host_of(url: str) -> str:
    """Nazwa hosta małymi literami (bez portu)."""
    return (urlsplit(url).hostname or "").lower()


def _retry_after_seconds(value: str | None, default: float) -> float:
    """Retry-After w sekundach (liczba albo data HTTP), co najmniej 1 s; bez nagłówka – `default`."""
    if not value:
        return default
    value = value.strip()
    if value.isdigit():
        return max(float(value), 1.0)
    try:
        dt = parsedate_to_datetime(value)
        return max((dt - datetime.now(timezone.utc)).total_seconds(), 1.0)
    except (TypeError, ValueError):
        return default


_TRACKERS = ("google-analytics.com", "googletagmanager.com", "doubleclick.net", "facebook.net",
             "facebook.com", "hotjar.com", "gemius.pl", "youtube.com", "ytimg.com", "twitter.com",
             "x.com", "linkedin.com", "clarity.ms")
_BLOCKED_RESOURCES = {"image", "media", "font", "stylesheet", "imageset", "texttrack", "manifest"}


class Fetcher:
    """Jedyna droga do sieci: pilnuje robots.txt, odstępów między zapytaniami i wycofań."""

    def __init__(self, settings: dict, store: Store, user_agent: str):
        self.settings = settings
        self.store = store
        self.ua = user_agent
        self.product_token = user_agent.split("/", 1)[0]
        self.client = httpx.Client(
            headers={"User-Agent": user_agent, "Accept-Language": "pl,en;q=0.5"},
            timeout=float(settings["http_timeout_seconds"]),
            follow_redirects=False,
        )
        self._last_request: dict[str, float] = {}
        self._robots: dict[str, tuple[RobotsRules, float]] = {}
        self._pw = None
        self._browser = None

    # ---------- limit zapytań ----------
    def _min_delay(self, host: str, crawl_delay: float | None) -> float:
        d = float(self.settings["min_delay_seconds"])
        if host not in LOCAL_HOSTS:
            d = max(d, HARD_MIN_DELAY)
        if crawl_delay:
            d = max(d, crawl_delay)
        return d

    def _wait_turn(self, host: str, crawl_delay: float | None = None) -> None:
        delay = self._min_delay(host, crawl_delay)
        last = self._last_request.get(host)
        if last is not None:
            wait = last + delay - time.monotonic()
            if wait > 0:
                time.sleep(wait)
        self._last_request[host] = time.monotonic()

    # ---------- wycofanie ----------
    def backoff_until(self, host: str) -> datetime | None:
        """Koniec aktywnego wycofania dla hosta (wygasłe usuwa ze stanu) albo None."""
        b = self.store.state["backoff"].get(host)
        if not b:
            return None
        until = parse_iso(b.get("until"))
        if until and until > now_utc():
            return until
        self.store.state["backoff"].pop(host, None)
        return None

    def _set_backoff(self, host: str, seconds: float, reason: str, kind: str = "throttled") -> datetime:
        until = now_utc() + timedelta(seconds=seconds)
        self.store.state["backoff"][host] = {"until": iso(until), "reason": reason, "kind": kind}
        self.store.save_state()
        log.warning("Wycofanie z %s do %s (%s)", host, until.isoformat(), reason)
        return until

    def _backoff_seconds(self, r: httpx.Response, setting: str) -> float:
        """Czas wycofania: Retry-After z odpowiedzi albo domyślna liczba minut z ustawień."""
        return _retry_after_seconds(r.headers.get("retry-after"), float(self.settings[setting]) * 60)

    def _challenge_backoff(self, host: str, marker: str) -> None:
        """Wyzwanie antybotowe traktujemy jak 403: wycofanie (kind=blocked), bez prób obejścia."""
        self._set_backoff(host, float(self.settings["forbidden_backoff_minutes"]) * 60,
                          f"wyzwanie antybotowe ({marker})", kind="blocked")

    def _check_backoff(self, host: str) -> None:
        until = self.backoff_until(host)
        if until:
            b = self.store.state["backoff"][host]
            exc = Blocked if b.get("kind") == "blocked" else Throttled
            raise exc(f"{host}: wycofanie do {until.isoformat()} ({b.get('reason', '')})")

    # ---------- robots.txt ----------
    def robots_for(self, url: str) -> RobotsRules:
        """Reguły robots.txt dla schematu+hosta adresu (z pamięci podręcznej z TTL).

        RFC 9309: 2xx → parsujemy, 4xx → brak ograniczeń, 5xx / błąd sieci → nic nie pobieramy
        z hosta przez `robots_unavailable_retry_minutes` (RobotsUnavailable)."""
        p = urlsplit(url)
        key = f"{p.scheme}://{p.netloc}"
        cached = self._robots.get(key)
        if cached and cached[1] > time.monotonic():
            if cached[0].disallow_all:  # zapamiętana niedostępność robots.txt (5xx / błąd sieci)
                raise RobotsUnavailable(f"robots.txt {key}/robots.txt był niedostępny – czekam do ponownej próby")
            return cached[0]
        host = (p.hostname or "").lower()
        self._check_backoff(host)
        robots_url = key + "/robots.txt"
        ttl_ok = float(self.settings["robots_ttl_hours"]) * 3600
        ttl_bad = float(self.settings["robots_unavailable_retry_minutes"]) * 60
        try:
            r = self._raw_get(robots_url, {}, check_robots=False)
            status = r.status_code
            if 200 <= status < 300:
                rules = parse_robots(r.text, self.product_token)
                ttl = ttl_ok
            elif status == 429:
                # 429 to nie „brak robots.txt” (jak inne 4xx), tylko prośba o zwolnienie → wycofanie
                self._set_backoff(host, self._backoff_seconds(r, "default_backoff_minutes"), "429 na robots.txt")
                raise Throttled(f"{host}: 429 na robots.txt")
            elif 400 <= status < 500:
                # RFC 9309: robots.txt niedostępny (4xx) = brak ograniczeń
                rules, ttl = RobotsRules(allow_all=True), ttl_ok
            else:
                log.warning("robots.txt %s → %s: wstrzymuję pobieranie z hosta", robots_url, status)
                self._robots[key] = (RobotsRules(disallow_all=True), time.monotonic() + ttl_bad)
                raise RobotsUnavailable(f"robots.txt {robots_url} zwrócił {status}")
        except (httpx.HTTPError, HttpError) as e:  # HttpError: pętla przekierowań robots.txt
            self._robots[key] = (RobotsRules(disallow_all=True), time.monotonic() + ttl_bad)
            raise RobotsUnavailable(f"robots.txt {robots_url}: błąd sieci {type(e).__name__}") from e
        self._robots[key] = (rules, time.monotonic() + ttl)
        return rules

    def cached_robots_allows(self, url: str) -> bool:
        """Bez zapytań sieciowych: czy zapamiętany robots.txt originu pozwala na adres
        (brak wpisu w pamięci = nie wiemy, więc nie blokujemy). Dla podzasobów w przeglądarce."""
        p = urlsplit(url)
        cached = self._robots.get(f"{p.scheme}://{p.netloc}")
        if not cached or cached[1] <= time.monotonic():
            return True
        return cached[0].allowed(url)

    def check_allowed(self, url: str) -> RobotsRules:
        """Rzuca RobotsBlocked, jeśli robots.txt zabrania adresu; zwraca reguły (Crawl-delay)."""
        rules = self.robots_for(url)
        if not rules.allowed(url):
            raise RobotsBlocked(f"robots.txt blokuje {url}")
        return rules

    # ---------- HTTP ----------
    def _raw_get(self, url: str, headers: dict, check_robots: bool = True,
                 max_redirects: int = 5, private: dict | None = None) -> httpx.Response:
        """GET z ręcznym śledzeniem przekierowań: każdy skok przechodzi robots + limit zapytań.
        `private` (np. klucz API) idzie tylko do hosta z `url`, nigdy po przekierowaniu na inny host."""
        current = url
        for _ in range(max_redirects + 1):
            host = host_of(current)
            self._check_backoff(host)
            delay = None
            if check_robots:
                delay = self.check_allowed(current).crawl_delay
            self._wait_turn(host, delay)
            r = self.client.get(current, headers={**headers, **private} if private and host == host_of(url) else headers)
            if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                # RFC 9309 pozwala śledzić do 5 przekierowań robots.txt (także na inny host)
                current = strip_fragment(urljoin(current, r.headers["location"]))
                continue
            return r
        raise HttpError(f"Za dużo przekierowań: {url}")

    def http_get(self, url: str, conditional: bool = True, accept: str | None = None,
                 private: dict | None = None) -> Resp:
        """GET z robots.txt, limitem zapytań i (opcjonalnie) If-None-Match / If-Modified-Since.
        `private`: dodatkowe nagłówki (np. klucz API) tylko dla hosta z `url`.

        429/503 → Throttled, 403 i wyzwania antybotowe → Blocked (oba ustawiają wycofanie hosta),
        inne ≥400 → HttpError. Walidatory zapisuje dopiero commit_validators()."""
        url = strip_fragment(url)
        host = host_of(url)
        headers: dict[str, str] = {}
        if accept:
            headers["Accept"] = accept
        if conditional:
            v = self.store.validators(url)
            if v.get("etag"):
                headers["If-None-Match"] = v["etag"]
            if v.get("last_modified"):
                headers["If-Modified-Since"] = v["last_modified"]
        try:
            r = self._raw_get(url, headers, private=private)
        except httpx.HTTPError as e:
            raise HttpError(f"{url}: {type(e).__name__}: {e}") from e
        status = r.status_code
        self._raise_for_backoff(url, host, r)
        if status == 304:
            return Resp(url=url, final_url=str(r.url), status=304, headers=dict(r.headers),
                        etag=r.headers.get("etag"), last_modified=r.headers.get("last-modified"))
        if status >= 300:  # 3xx z Location obsługuje _raw_get; tu zostają 3xx bez Location i błędy
            raise HttpError(f"{url}: HTTP {status}", status)
        return self._to_resp(url, host, r)

    def _raise_for_backoff(self, url: str, host: str, r: httpx.Response) -> None:
        """429/503 → Throttled, 403 → Blocked; oba zapisują wycofanie hosta."""
        status = r.status_code
        if status in (429, 503):
            secs = self._backoff_seconds(r, "default_backoff_minutes")
            self._set_backoff(host, secs, f"HTTP {status}")
            raise Throttled(f"{url}: HTTP {status}, wycofanie na {int(secs)} s")
        if status == 403:
            secs = self._backoff_seconds(r, "forbidden_backoff_minutes")
            self._set_backoff(host, secs, "HTTP 403", kind="blocked")
            raise Blocked(f"{url}: HTTP 403 (odmowa dostępu) – nie obchodzimy, wycofanie na {int(secs)} s")

    def _to_resp(self, url: str, host: str, r: httpx.Response) -> Resp:
        """Odpowiedź 2xx → Resp (rozpakowanie gzip, dekodowanie, detekcja wyzwań antybotowych)."""
        status = r.status_code
        content = r.content
        if content[:2] == b"\x1f\x8b":
            try:
                content = gzip.decompress(content)
            except OSError:
                pass
        resp = Resp(url=url, final_url=strip_fragment(str(r.url)), status=status,
                    headers={k.lower(): v for k, v in r.headers.items()}, content=content,
                    etag=r.headers.get("etag"), last_modified=r.headers.get("last-modified"))
        try:
            resp.text = content.decode(r.encoding or "utf-8", errors="replace")
        except LookupError:
            resp.text = content.decode("utf-8", errors="replace")
        if resp.is_html:
            hit = looks_like_challenge(resp.text, status)
            if hit:
                self._challenge_backoff(host, hit)
                raise Blocked(f"{url}: wyzwanie antybotowe/CAPTCHA (znacznik {hit!r}) – nie obchodzimy")
        return resp

    def http_send(self, method: str, url: str, json_body: object = None, accept: str | None = None,
                  headers: dict | None = None, private: dict | None = None) -> Resp:
        """Zapytanie inne niż GET (np. POST JSON-RPC do serwera MCP) z tymi samymi zasadami co http_get:
        robots.txt, wycofanie hosta, odstęp między zapytaniami. Bez przekierowań (3xx → HttpError),
        więc `private` (klucz API) nigdy nie trafia na inny host. Status ≥400 → HttpError z `status`
        i początkiem odpowiedzi (np. komunikatem JSON-RPC o złym kluczu)."""
        url = strip_fragment(url)
        host = host_of(url)
        self._check_backoff(host)
        delay = self.check_allowed(url).crawl_delay
        self._wait_turn(host, delay)
        hdrs = {**(headers or {}), **(private or {})}
        if accept:
            hdrs["Accept"] = accept
        if any(k.lower() == "user-agent" for k in hdrs):
            raise ValueError("nie wolno zmieniać User-Agenta")
        try:
            r = self.client.request(method, url, json=json_body, headers=hdrs)
        except httpx.HTTPError as e:
            raise HttpError(f"{method} {url}: {type(e).__name__}: {e}") from e
        self._raise_for_backoff(url, host, r)
        if r.status_code >= 300:
            snippet = re.sub(r"\s+", " ", r.text[:300]).strip()
            raise HttpError(f"{method} {url}: HTTP {r.status_code} {snippet}".rstrip(), r.status_code)
        return self._to_resp(url, host, r)

    def commit_validators(self, resp: Resp) -> None:
        """Zapamiętaj ETag/Last-Modified dopiero po udanym przetworzeniu odpowiedzi."""
        if resp.status == 304:
            return
        self.store.set_validators(resp.url, resp.etag, resp.last_modified)

    # ---------- Playwright ----------
    def _ensure_browser(self):
        if self._browser is None:
            from playwright.sync_api import sync_playwright
            self._pw = sync_playwright().start()
            # handle_sigint=False: Ctrl+C obsługujemy sami (kończymy bieżące źródło)
            self._browser = self._pw.chromium.launch(headless=True, handle_sigint=False,
                                                     handle_sigterm=False, handle_sighup=False)
        return self._browser

    def render(self, url: str, conditional: bool = True) -> Resp:
        """Dokument główny pobieramy sami (robots, limit, If-None-Match), a przeglądarce podajemy go
        przez route.fulfill – dzięki temu nie ma podwójnego zapytania, a JS strony się wykonuje."""
        resp = self.http_get(url, conditional=conditional, accept="text/html,application/xhtml+xml")
        if resp.not_modified or not resp.is_html:
            return resp
        resp.text = self._render_html(resp.final_url, resp.content, resp.headers.get("content-type", "text/html"))
        resp.method = "playwright"
        hit = looks_like_challenge(resp.text)
        if hit:
            self._challenge_backoff(host_of(url), hit)
            raise Blocked(f"{url}: wyzwanie antybotowe/CAPTCHA po renderowaniu (znacznik {hit!r})")
        return resp

    def _render_html(self, url: str, body: bytes, content_type: str) -> str:
        """Renderuje pobrany już dokument. Inne nawigacje, obrazy/czcionki/style, trackery
        i zasoby (poza skryptami/XHR) z obcych hostów są blokowane."""
        from playwright.sync_api import TimeoutError as PWTimeout
        browser = self._ensure_browser()
        ctx = browser.new_context(user_agent=self.ua, locale="pl-PL", service_workers="block",
                                  java_script_enabled=True)
        try:
            page = ctx.new_page()
            target_host = host_of(url)

            def handler(route, request):
                try:
                    if request.is_navigation_request() and strip_fragment(request.url) == url \
                            and request.frame == page.main_frame:
                        route.fulfill(status=200, headers={"content-type": content_type}, body=body)
                        return
                    rhost = host_of(request.url)
                    if request.resource_type in _BLOCKED_RESOURCES or any(
                            rhost == t or rhost.endswith("." + t) for t in _TRACKERS):
                        route.abort()
                        return
                    if request.is_navigation_request() and request.frame == page.main_frame:
                        route.abort()  # JS próbuje przejść gdzie indziej – nie podążamy
                        return
                    if rhost != target_host and request.resource_type not in ("script", "xhr", "fetch"):
                        route.abort()
                        return
                    if not self.cached_robots_allows(request.url):
                        route.abort()  # skrypt strony chce pobrać adres zabroniony w robots.txt
                        return
                    route.continue_()
                except Exception:  # strona zamknięta w trakcie
                    pass

            page.route("**/*", handler)
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            try:
                page.wait_for_load_state("networkidle", timeout=float(self.settings["render_timeout_seconds"]) * 1000)
            except PWTimeout:
                log.debug("networkidle nie nastąpił w limicie dla %s – biorę to, co jest", url)
            return page.content()
        finally:
            ctx.close()

    def close(self) -> None:
        """Zamyka klienta HTTP i przeglądarkę (jeśli została uruchomiona)."""
        try:
            self.client.close()
        finally:
            if self._browser is not None:
                try:
                    self._browser.close()
                    self._pw.stop()
                except Exception:
                    pass
                self._browser = self._pw = None
