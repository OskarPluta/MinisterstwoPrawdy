"""One-off fetch of an address the user sent, with the crawler's manners and SSRF protection.

Same rules as the crawler (FaktBot): honest User-Agent with a contact address, robots.txt checked
for every address and every redirect, no proxies, no stealth, no logging in, no JavaScript, and an
anti-bot challenge or 403/429/503 ends the attempt. On top of that, because the address comes from
a stranger and this server can reach internal services, every host is resolved and must be a
public IP, ports are limited to 80/443, and the body size and time are capped.

Every failure is a FetchFailed with a Polish message for the user (a limitation, not a verdict).
"""

from __future__ import annotations

import ipaddress
import logging
import os
import socket
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from crawler import __version__ as crawler_version
from crawler.config import EMAIL_RE
from crawler.fetcher import looks_like_challenge
from crawler.robots import RobotsRules, parse_robots

log = logging.getLogger(__name__)

PRODUCT_TOKEN = "FaktBot"  # same token as the crawler, so a site that blocks FaktBot blocks this too
MAX_REDIRECTS = 3
MAX_BYTES = 5_000_000
TIMEOUT_S = 10.0
MIN_DELAY_S = 3.0  # between page requests to one host, across all users
ROBOTS_TTL_S = 3600.0

Resolver = Callable[[str, int], list[str]]


class FetchFailed(Exception):
    """`message` is for the user (Polish); `reason` is a short code for logs and tests."""

    def __init__(self, message: str, reason: str):
        super().__init__(message)
        self.message = message
        self.reason = reason


@dataclass
class Fetched:
    url: str  # what was asked for
    final_url: str  # after redirects
    status: int
    content_type: str  # lower-case, without parameters
    body: bytes
    truncated: bool  # the body hit max_bytes

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    @property
    def is_html(self) -> bool:
        return self.content_type in ("text/html", "application/xhtml+xml")


def contact_from_env() -> str | None:
    c = (os.environ.get("INTAKE_CONTACT") or os.environ.get("CRAWLER_CONTACT") or "").strip()
    return c if EMAIL_RE.match(c) else None


def user_agent(contact: str) -> str:
    return f"{PRODUCT_TOKEN}/{crawler_version} (+{contact}; pobranie na prośbę użytkownika)"


def _system_resolver(host: str, port: int) -> list[str]:
    return [str(info[4][0]) for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)]


def is_public_ip(value: str) -> bool:
    ip = ipaddress.ip_address(value.split("%", 1)[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


MSG_PRIVATE = "Ten adres prowadzi do sieci wewnętrznej, więc go nie pobieramy."
MSG_BAD_URL = "To nie jest poprawny adres strony internetowej (obsługujemy tylko http i https)."


class SafeFetcher:
    """`allow_private` exists for tests against a local server only; never set it in a deployment."""

    def __init__(
        self, user_agent: str, *, allow_private: bool = False, timeout: float = TIMEOUT_S,
        max_bytes: int = MAX_BYTES, min_delay: float = MIN_DELAY_S, resolver: Resolver = _system_resolver,
        transport: httpx.BaseTransport | None = None,
    ):
        self.ua = user_agent
        self.allow_private = allow_private
        self.max_bytes = max_bytes
        self.min_delay = min_delay
        self.resolver = resolver
        self.client = httpx.Client(
            headers={"User-Agent": user_agent, "Accept-Language": "pl,en;q=0.5"},
            timeout=timeout, follow_redirects=False, transport=transport, trust_env=False,  # no proxies
        )
        self._lock = threading.Lock()
        self._last: dict[str, float] = {}
        self._robots: dict[str, tuple[RobotsRules, float]] = {}

    # ---------- checks ----------
    def check_url(self, url: str) -> str:
        """Normalised URL (no fragment) or FetchFailed. Resolves the host and checks every address."""
        try:
            p = urlsplit(url.strip())
            port = p.port
        except ValueError:
            raise FetchFailed(MSG_BAD_URL, "bad_url") from None
        if p.scheme not in ("http", "https") or not p.hostname:
            raise FetchFailed(MSG_BAD_URL, "bad_url")
        if p.username or p.password:
            raise FetchFailed("Adres zawiera login lub hasło. Takich adresów nie pobieramy.", "credentials")
        if not self.allow_private:
            if port not in (None, 80, 443):
                raise FetchFailed("Pobieramy tylko strony na standardowych portach (80 i 443).", "port")
            try:
                addrs = self.resolver(p.hostname, port or (443 if p.scheme == "https" else 80))
            except (OSError, UnicodeError):
                raise FetchFailed("Nie znaleziono serwera o tej nazwie.", "dns") from None
            if not addrs or not all(is_public_ip(a) for a in addrs):
                raise FetchFailed(MSG_PRIVATE, "private_address")
        return urlunsplit((p.scheme, p.netloc, p.path or "/", p.query, ""))

    def _check_peer(self, r: httpx.Response) -> None:
        """Best effort against DNS rebinding: the address actually connected to must be public too."""
        if self.allow_private:
            return
        stream = r.extensions.get("network_stream")
        try:
            addr = stream.get_extra_info("server_addr") if stream is not None else None
        except Exception:  # noqa: BLE001 - not every transport exposes it
            addr = None
        if addr and not is_public_ip(str(addr[0])):
            raise FetchFailed(MSG_PRIVATE, "private_address")

    # ---------- robots.txt ----------
    def robots_for(self, url: str) -> RobotsRules:
        """RFC 9309, like the crawler: 2xx parse, 4xx no limits, 5xx or network error: do not fetch."""
        p = urlsplit(url)
        key = f"{p.scheme}://{p.netloc}"
        with self._lock:
            cached = self._robots.get(key)
        if cached and cached[1] > time.monotonic():
            return cached[0]
        unavailable = FetchFailed(
            "Nie udało się sprawdzić, czy strona pozwala na automatyczne pobieranie (robots.txt), "
            "więc jej nie pobieramy.", "robots_unavailable",
        )
        current = key + "/robots.txt"
        try:
            for _ in range(5 + 1):  # RFC 9309: follow up to five redirects
                self.check_url(current)
                r = self.client.get(current)
                if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                    current = urljoin(current, r.headers["location"])
                    continue
                break
            else:
                raise unavailable
        except httpx.HTTPError:
            raise unavailable from None
        if 200 <= r.status_code < 300:
            rules = parse_robots(r.text, PRODUCT_TOKEN)
        elif r.status_code == 429:
            raise FetchFailed("Serwis prosi o ograniczenie liczby zapytań. Spróbuj później.", "throttled")
        elif 400 <= r.status_code < 500:
            rules = RobotsRules(allow_all=True)
        else:
            raise unavailable
        with self._lock:
            self._robots[key] = (rules, time.monotonic() + ROBOTS_TTL_S)
        return rules

    def _wait_turn(self, host: str) -> None:
        with self._lock:
            wait = self._last.get(host, 0.0) + self.min_delay - time.monotonic()
            self._last[host] = time.monotonic() + max(wait, 0.0)
        if wait > 0:
            time.sleep(wait)

    # ---------- GET ----------
    def get(self, url: str, *, accept: str | None = None, private_headers: dict[str, str] | None = None,
            check_robots: bool = True) -> Fetched:
        """`private_headers` (an API key) go only to the host of `url`, never after a redirect elsewhere."""
        first = self.check_url(url)
        current = first
        origin_host = urlsplit(first).hostname
        for _ in range(MAX_REDIRECTS + 1):
            host = urlsplit(current).hostname or ""
            if check_robots and not self.robots_for(current).allowed(current):
                raise FetchFailed(
                    "Właściciel strony nie zezwala na automatyczne pobieranie tego adresu (robots.txt). "
                    "Wklej treść strony albo prześlij zrzut ekranu.", "robots",
                )
            self._wait_turn(host)
            headers = {"Accept": accept} if accept else {}
            if private_headers and host == origin_host:
                headers.update(private_headers)
            try:
                with self.client.stream("GET", current, headers=headers) as r:
                    self._check_peer(r)
                    if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                        current = self.check_url(urljoin(current, r.headers["location"]))
                        continue
                    body, truncated = self._read(r)
                    return self._finish(first, current, r, body, truncated)
            except httpx.TimeoutException:
                raise FetchFailed("Strona nie odpowiedziała w wyznaczonym czasie.", "timeout") from None
            except httpx.HTTPError as e:
                log.info("fetch %s: %s", host, type(e).__name__)
                raise FetchFailed("Nie udało się połączyć ze stroną.", "network") from None
        raise FetchFailed("Strona przekierowuje zbyt wiele razy.", "redirects")

    def _read(self, r: httpx.Response) -> tuple[bytes, bool]:
        chunks: list[bytes] = []
        size = 0
        for chunk in r.iter_bytes():
            chunks.append(chunk)
            size += len(chunk)
            if size > self.max_bytes:
                return b"".join(chunks)[: self.max_bytes], True
        return b"".join(chunks), False

    def _finish(self, first: str, final: str, r: httpx.Response, body: bytes, truncated: bool) -> Fetched:
        ctype = r.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        f = Fetched(url=first, final_url=final, status=r.status_code, content_type=ctype, body=body, truncated=truncated)
        if f.is_html and (marker := looks_like_challenge(f.text, r.status_code)):
            log.info("challenge page at %s (%s)", urlsplit(final).hostname, marker)
            raise FetchFailed(
                "Strona wymaga potwierdzenia, że nie jesteś robotem. Takich zabezpieczeń nie obchodzimy. "
                "Wklej treść strony albo prześlij zrzut ekranu.", "blocked",
            )
        if r.status_code in (403, 429, 503):
            raise FetchFailed(
                f"Serwis odmówił dostępu (kod {r.status_code}). Wklej treść strony albo prześlij zrzut ekranu.",
                "refused",
            )
        if r.status_code >= 400:
            raise FetchFailed(f"Strona zwróciła błąd (kod {r.status_code}).", "http_error")
        return f

    def close(self) -> None:
        self.client.close()
