"""Safe fetching of user links, page extraction and YouTube metadata (local server / mock transport)."""

from __future__ import annotations

import json

import httpx
import pytest
from intake_helpers import HTML, FakeTranscriber, article_html, local_fetcher, make_image, serve, text_pdf

from intake.core import Intake
from intake.fetch import FetchFailed, SafeFetcher, contact_from_env, is_public_ip, user_agent
from intake.ocr import make_ocr
from intake.web import find_urls, in_catalog, read_url
from intake.youtube import LIMIT_NO_RECORDING, LIMIT_OEMBED, read_youtube, video_id

LONG = "Rząd ogłosił, że od poniedziałku wszystkie szkoły w kraju przechodzą na naukę zdalną. " * 3
ROBOTS = (200, {"Content-Type": "text/plain"}, b"User-agent: *\nDisallow: /private/\n")


def ocr():
    return make_ocr(FakeTranscriber(), "llm")


# ---------------- address checks (no network: fake resolver) ----------------

def _resolver(table: dict[str, list[str]]):
    def resolve(host: str, port: int) -> list[str]:
        if host not in table:
            raise OSError("nxdomain")
        return table[host]
    return resolve


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.0.0.5", "172.16.1.1", "192.168.1.1", "169.254.169.254",
                                "0.0.0.0", "::1", "::ffff:127.0.0.1", "fd00::1", "100.64.0.1"])
def test_private_addresses_are_refused(ip):
    f = SafeFetcher("FaktBot/test (+t@example.test)", resolver=_resolver({"evil.test": [ip]}))
    with pytest.raises(FetchFailed) as e:
        f.check_url("http://evil.test/")
    assert e.value.reason == "private_address"


def test_one_private_address_among_public_is_refused():
    f = SafeFetcher("ua", resolver=_resolver({"mixed.test": ["93.184.216.34", "10.0.0.1"]}))
    with pytest.raises(FetchFailed):
        f.check_url("https://mixed.test/")


@pytest.mark.parametrize("url, reason", [
    ("ftp://example.test/x", "bad_url"),
    ("javascript:alert(1)", "bad_url"),
    ("https://user:pass@public.test/", "credentials"),
    ("https://public.test:8080/", "port"),
    ("https://nope.test/", "dns"),
])
def test_bad_urls(url, reason):
    f = SafeFetcher("ua", resolver=_resolver({"public.test": ["93.184.216.34"]}))
    with pytest.raises(FetchFailed) as e:
        f.check_url(url)
    assert e.value.reason == reason


def test_public_address_passes_and_fragment_is_dropped():
    f = SafeFetcher("ua", resolver=_resolver({"public.test": ["93.184.216.34", "2606:2800:220:1::1"]}))
    assert f.check_url("https://public.test/a?b=1#frag") == "https://public.test/a?b=1"
    assert is_public_ip("8.8.8.8") and not is_public_ip("::ffff:10.0.0.1")


def _mock_fetcher(handler, table=None):
    table = table or {"public.test": ["93.184.216.34"], "other.test": ["93.184.216.35"], "internal.test": ["10.0.0.1"]}
    return SafeFetcher("FaktBot/test (+t@example.test)", resolver=_resolver(table), min_delay=0,
                       transport=httpx.MockTransport(handler))


def test_redirect_to_private_network_is_refused():
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(302, headers={"location": "http://internal.test/admin"})

    with pytest.raises(FetchFailed) as e:
        _mock_fetcher(handler).get("https://public.test/go")
    assert e.value.reason == "private_address"


def test_private_headers_do_not_follow_a_redirect_to_another_host():
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        if req.url.host == "public.test":
            return httpx.Response(302, headers={"location": "https://other.test/page"})
        return httpx.Response(200, headers={"content-type": "application/json"}, content=b"{}")

    _mock_fetcher(handler).get("https://public.test/api", private_headers={"X-Goog-Api-Key": "SECRET"})
    pages = [r for r in seen if r.url.path != "/robots.txt"]
    assert pages[0].headers.get("x-goog-api-key") == "SECRET"
    assert "x-goog-api-key" not in pages[1].headers
    assert all("SECRET" not in str(r.url) for r in seen)
    assert pages[0].headers["user-agent"].startswith("FaktBot/")


def test_contact_and_user_agent(monkeypatch):
    monkeypatch.delenv("CRAWLER_CONTACT", raising=False)
    monkeypatch.setenv("INTAKE_CONTACT", "not-an-email")
    assert contact_from_env() is None
    monkeypatch.setenv("INTAKE_CONTACT", "zespol@example.test")
    assert contact_from_env() == "zespol@example.test"
    ua = user_agent("zespol@example.test")
    assert ua.startswith("FaktBot/") and "+zespol@example.test" in ua


# ---------------- pages from a local server ----------------

def test_html_page_is_extracted_with_its_date():
    routes = {"/robots.txt": ROBOTS, "/news/1": (200, {"Content-Type": HTML}, article_html("Szkoły zamknięte", LONG))}
    with serve(routes) as (base, _):
        a = read_url(base + "/news/1", local_fetcher(), ocr())
    assert a.kind == "url" and a.method == "html" and a.readable
    assert a.text.startswith("Tytuł strony: Szkoły zamknięte") and "nauk" in a.text
    assert a.material_date and a.material_date.startswith("2019-03-12") and a.material_date_kind == "published"
    assert any("12.03.2019" in n for n in a.notes)
    assert a.meta["sha256"] and a.limitations == []


def test_robots_txt_is_respected():
    routes = {"/robots.txt": ROBOTS, "/private/x": (200, {"Content-Type": HTML}, article_html("T", LONG))}
    with serve(routes) as (base, seen):
        a = read_url(base + "/private/x", local_fetcher(), ocr())
    assert not a.readable and "robots.txt" in a.limitations[0]
    assert [r["path"] for r in seen] == ["/robots.txt"]  # the page itself was never requested


def test_robots_404_allows_and_5xx_blocks():
    page = (200, {"Content-Type": HTML}, article_html("T", LONG))
    with serve({"/p": page}) as (base, _):
        assert read_url(base + "/p", local_fetcher(), ocr()).readable
    with serve({"/robots.txt": (500, {}, b""), "/p": page}) as (base, seen):
        a = read_url(base + "/p", local_fetcher(), ocr())
    assert not a.readable and "robots.txt" in a.limitations[0] and len(seen) == 1


def test_redirect_is_followed_and_reported_and_user_agent_is_honest():
    with serve({"/robots.txt": ROBOTS}) as (target, _):
        routes = {"/robots.txt": ROBOTS, "/s": (301, {"Location": target + "/missing"}, b"")}
        with serve(routes) as (base, seen):
            a = read_url(base + "/s", local_fetcher(), ocr())
    assert "błąd (kod 404)" in a.limitations[0]
    assert all(r["headers"]["User-Agent"].startswith("FaktBot/") for r in seen)


@pytest.mark.parametrize("status, html, expect", [
    (403, b"<html>forbidden</html>", "odmówił dostępu (kod 403)"),
    (429, b"<html>slow down</html>", "odmówił dostępu (kod 429)"),
    (200, b"<html><title>Just a moment...</title>cf-browser-verification</html>", "nie jesteś robotem"),
])
def test_refusals_and_challenges_become_limitations(status, html, expect):
    with serve({"/robots.txt": ROBOTS, "/p": (status, {"Content-Type": HTML}, html)}) as (base, _):
        a = read_url(base + "/p", local_fetcher(), ocr())
    assert not a.readable and expect in a.limitations[0]


def test_empty_page_and_unsupported_type_and_truncation():
    routes = {
        "/robots.txt": ROBOTS,
        "/js": (200, {"Content-Type": HTML}, b"<html><body><div id=app></div><script>x()</script></body></html>"),
        "/zip": (200, {"Content-Type": "application/zip"}, b"PK\x03\x04" + b"0" * 50),
        "/big": (200, {"Content-Type": HTML}, article_html("Duża", LONG * 50)),
    }
    with serve(routes) as (base, _):
        js = read_url(base + "/js", local_fetcher(), ocr())
        z = read_url(base + "/zip", local_fetcher(), ocr())
        big = read_url(base + "/big", local_fetcher(max_bytes=2000), ocr())
    assert not js.readable and "JavaScriptu" in js.limitations[0]
    assert not z.readable and "application/zip" in z.limitations[0]
    assert any("tylko jej początek" in n for n in big.limitations)


def test_pdf_and_image_behind_a_link():
    routes = {
        "/robots.txt": ROBOTS,
        "/doc.pdf": (200, {"Content-Type": "application/pdf"}, text_pdf(["Komunikat nr 7 z dnia 12 marca 2019"] * 8)),
        "/img": (200, {"Content-Type": "image/png"}, make_image()),
    }
    tr = FakeTranscriber()
    with serve(routes) as (base, _):
        pdf = read_url(base + "/doc.pdf", local_fetcher(), make_ocr(tr, "llm"))
        img = read_url(base + "/img", local_fetcher(), make_ocr(tr, "llm"))
    assert pdf.kind == "pdf" and pdf.method == "pdf_text" and "Komunikat nr 7" in pdf.text
    assert img.kind == "image" and img.method == "ocr_llm" and "zamknięte szkoły" in img.text
    assert len(tr.calls) == 1


def test_catalog_domain_gives_a_neutral_note_only():
    routes = {"/robots.txt": ROBOTS, "/p": (200, {"Content-Type": HTML}, article_html("T", LONG))}
    with serve(routes) as (base, _):
        a = read_url(base + "/p", local_fetcher(), ocr(), known_domains=lambda: {"127.0.0.1"})
    assert any("katalogu źródeł" in n for n in a.notes)
    assert in_catalog("komunikaty.gov.pl", {"gov.pl"}) == "gov.pl" and in_catalog("evilgov.pl", {"gov.pl"}) is None


def test_no_fetcher_means_a_stated_limitation():
    a = Intake(None).read_link("https://example.test/x", None)
    assert not a.readable and "nie jest skonfigurowane" in a.limitations[0]


# ---------------- links in text ----------------

def test_find_urls():
    text = "Zobacz https://a.test/x?y=1, oraz www.b.test/z). I jeszcze https://a.test/x?y=1 i https://c.test/."
    assert find_urls(text) == ["https://a.test/x?y=1", "https://www.b.test/z"]
    assert find_urls("brak linków") == []
    assert find_urls("„https://a.test/p”") == ["https://a.test/p"]


# ---------------- YouTube ----------------

@pytest.mark.parametrize("url, vid", [
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=10", "dQw4w9WgXcQ"),
    ("https://youtu.be/dQw4w9WgXcQ?si=abc", "dQw4w9WgXcQ"),
    ("https://m.youtube.com/shorts/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://www.youtube.com/embed/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://www.youtube.com/live/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://www.youtube.com/watch?v=short", None),
    ("https://www.youtube.com/@kanal", None),
    ("https://notyoutube.com/watch?v=dQw4w9WgXcQ", None),
])
def test_video_id(url, vid):
    assert video_id(url) == vid


def _yt_fetcher(seen: list[httpx.Request]):
    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        if req.url.host == "www.googleapis.com":
            body = {"items": [{"snippet": {"title": "Powódź w Sandomierzu", "channelTitle": "Kanał X",
                                           "description": "Nagranie z wczoraj.", "publishedAt": "2010-05-20T08:00:00Z"}}]}
            return httpx.Response(200, json=body)
        return httpx.Response(200, json={"title": "Powódź w Sandomierzu", "author_name": "Kanał X"})

    table = {"www.googleapis.com": ["142.250.0.1"], "www.youtube.com": ["142.250.0.2"]}
    return SafeFetcher("FaktBot/test (+t@example.test)", resolver=_resolver(table), min_delay=0,
                       transport=httpx.MockTransport(handler))


def test_youtube_with_data_api_key():
    seen: list[httpx.Request] = []
    a = read_youtube("https://youtu.be/dQw4w9WgXcQ", "dQw4w9WgXcQ", _yt_fetcher(seen), "KEY123")
    assert a.method == "youtube_data_api" and "Tytuł filmu: Powódź w Sandomierzu" in a.text
    assert "Opis filmu:\nNagranie z wczoraj." in a.text
    assert a.material_date == "2010-05-20T08:00:00Z" and a.material_date_kind == "published"
    assert a.limitations == [LIMIT_NO_RECORDING]
    api = [r for r in seen if r.url.host == "www.googleapis.com" and r.url.path != "/robots.txt"][0]
    assert api.headers["x-goog-api-key"] == "KEY123" and "KEY123" not in str(api.url)


def test_youtube_without_key_uses_oembed():
    seen: list[httpx.Request] = []
    a = read_youtube("https://youtu.be/dQw4w9WgXcQ", "dQw4w9WgXcQ", _yt_fetcher(seen), None)
    assert a.method == "youtube_oembed" and "Kanał: Kanał X" in a.text and a.material_date is None
    assert LIMIT_OEMBED in a.limitations and LIMIT_NO_RECORDING in a.limitations
    oembed = [r for r in seen if r.url.path == "/oembed"][0]
    assert json.dumps(str(oembed.url)).count("dQw4w9WgXcQ") == 1
