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
    pass


class RobotsBlocked(FetchError):
    """Adres zablokowany w robots.txt – pomijamy (to nie błąd)."""


class RobotsUnavailable(FetchError):
    """robots.txt zwrócił 5xx / błąd sieci – na razie nic nie pobieramy z tego hosta."""


class Throttled(FetchError):
    """429/503 lub aktywne wycofanie dla hosta."""


class Blocked(FetchError):
    """403 albo wyzwanie antybotowe / CAPTCHA. Nie obchodzimy."""


class HttpError(FetchError):
    pass


@dataclass
class Resp:
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
    p = urlsplit(url)
    return urlunsplit((p.scheme, p.netloc, p.path or "/", p.query, ""))


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def _retry_after_seconds(value: str | None, default: float) -> float:
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

    def _check_backoff(self, host: str) -> None:
        until = self.backoff_until(host)
        if until:
            b = self.store.state["backoff"][host]
            exc = Blocked if b.get("kind") == "blocked" else Throttled
            raise exc(f"{host}: wycofanie do {until.isoformat()} ({b.get('reason', '')})")

    # ---------- robots.txt ----------
    def robots_for(self, url: str) -> RobotsRules:
        p = urlsplit(url)
        key = f"{p.scheme}://{p.netloc}"
        cached = self._robots.get(key)
        if cached and cached[1] > time.monotonic():
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
            elif 400 <= status < 500:
                # RFC 9309: 4xx (także 429? – nie: 429 traktujemy jak wycofanie) = brak ograniczeń
                if status == 429:
                    self._set_backoff(host, _retry_after_seconds(r.headers.get("retry-after"),
                                      self.settings["default_backoff_minutes"] * 60), "429 na robots.txt")
                    raise Throttled(f"{host}: 429 na robots.txt")
                rules, ttl = RobotsRules(allow_all=True), ttl_ok
            else:
                log.warning("robots.txt %s → %s: wstrzymuję pobieranie z hosta", robots_url, status)
                rules, ttl = RobotsRules(disallow_all=True), ttl_bad
                self._robots[key] = (rules, time.monotonic() + ttl)
                raise RobotsUnavailable(f"robots.txt {robots_url} zwrócił {status}")
        except httpx.HTTPError as e:
            rules = RobotsRules(disallow_all=True)
            self._robots[key] = (rules, time.monotonic() + ttl_bad)
            raise RobotsUnavailable(f"robots.txt {robots_url}: błąd sieci {type(e).__name__}") from e
        self._robots[key] = (rules, time.monotonic() + ttl)
        return rules

    def check_allowed(self, url: str) -> RobotsRules:
        rules = self.robots_for(url)
        if not rules.allowed(url):
            raise RobotsBlocked(f"robots.txt blokuje {url}")
        return rules

    # ---------- HTTP ----------
    def _raw_get(self, url: str, headers: dict, check_robots: bool = True,
                 max_redirects: int = 5) -> httpx.Response:
        """GET z ręcznym śledzeniem przekierowań: każdy skok przechodzi robots + limit zapytań."""
        current = url
        for _ in range(max_redirects + 1):
            host = host_of(current)
            self._check_backoff(host)
            delay = None
            if check_robots:
                delay = self.check_allowed(current).crawl_delay
            self._wait_turn(host, delay)
            r = self.client.get(current, headers=headers)
            if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                # RFC 9309 pozwala śledzić do 5 przekierowań robots.txt (także na inny host)
                current = strip_fragment(urljoin(current, r.headers["location"]))
                continue
            return r
        raise HttpError(f"Za dużo przekierowań: {url}")

    def http_get(self, url: str, conditional: bool = True, accept: str | None = None) -> Resp:
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
            r = self._raw_get(url, headers)
        except httpx.HTTPError as e:
            raise HttpError(f"{url}: {type(e).__name__}: {e}") from e
        status = r.status_code
        if status in (429, 503):
            secs = _retry_after_seconds(r.headers.get("retry-after"),
                                        float(self.settings["default_backoff_minutes"]) * 60)
            self._set_backoff(host, secs, f"HTTP {status}")
            raise Throttled(f"{url}: HTTP {status}, wycofanie na {int(secs)} s")
        if status == 403:
            secs = _retry_after_seconds(r.headers.get("retry-after"),
                                        float(self.settings["forbidden_backoff_minutes"]) * 60)
            self._set_backoff(host, secs, "HTTP 403", kind="blocked")
            raise Blocked(f"{url}: HTTP 403 (odmowa dostępu) – nie obchodzimy, wycofanie na {int(secs)} s")
        if status == 304:
            return Resp(url=url, final_url=str(r.url), status=304, headers=dict(r.headers),
                        etag=r.headers.get("etag"), last_modified=r.headers.get("last-modified"))
        if status >= 400:
            raise HttpError(f"{url}: HTTP {status}")
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
                self._set_backoff(host, float(self.settings["forbidden_backoff_minutes"]) * 60,
                                  f"wyzwanie antybotowe ({hit})", kind="blocked")
                raise Blocked(f"{url}: wyzwanie antybotowe/CAPTCHA (znacznik {hit!r}) – nie obchodzimy")
        return resp

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
            self._set_backoff(host_of(url), float(self.settings["forbidden_backoff_minutes"]) * 60,
                              f"wyzwanie antybotowe ({hit})", kind="blocked")
            raise Blocked(f"{url}: wyzwanie antybotowe/CAPTCHA po renderowaniu (znacznik {hit!r})")
        return resp

    def _render_html(self, url: str, body: bytes, content_type: str) -> str:
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
