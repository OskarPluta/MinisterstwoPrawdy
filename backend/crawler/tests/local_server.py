"""Lokalny serwer testowy symulujący źródła (bez internetu).

Ścieżki:
  /robots.txt                blokuje /private/
  /feed.xml                  RSS: wpis do /news/policja-1 (z danymi osobowymi) i do /private/tajne
  /news/<slug>               artykuły HTML (ETag + 304)
  /web/jednostka/aktualnosci lista w stylu gov.pl: menu bez dat, kafelki z datą obok linku i w linku, PDF
  /sitemap.xml               sitemapindex → /sitemap-news.xml (jeden adres z obcej domeny)
  /api/warnings              JSON z ostrzeżeniami
  /static/info               strona statyczna
  /js/page                   treść wstrzykiwana przez JavaScript (fetch do /api/js-content)
  /limited/feed.xml          zawsze 429 (Retry-After: 120)

Żądania z nagłówkiem Host zaczynającym się od ROBOTS_5XX_HOST dostają 500 na /robots.txt.
"""
from __future__ import annotations

import hashlib
import json
import socket
import threading
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HITS: Counter = Counter()          # (path, status) -> liczba
STATE_DEFAULTS = {
    "policja_extra": "",
    "warning_level": "2",
}
STATE = dict(STATE_DEFAULTS)
ROBOTS_5XX_HOST = "[::1]"          # host (z nagłówka Host), którego robots.txt odpowiada 500


def reset() -> None:
    """Zeruje liczniki żądań i przywraca domyślną treść stron."""
    HITS.clear()
    STATE.clear()
    STATE.update(STATE_DEFAULTS)


def article(title: str, body: str, date: str = "2026-11-21T10:00:00+01:00") -> str:
    return f"""<!doctype html><html lang="pl"><head><meta charset="utf-8"><title>{title} - Portal testowy</title>
<meta property="article:published_time" content="{date}"></head><body>
<header><nav><a href="/">Start</a> <a href="/web/jednostka/kontakt">Kontakt</a></nav></header>
<main><article><h1>{title}</h1>{body}</article></main>
<footer>Stopka serwisu. Wszelkie prawa zastrzeżone. <a href="/polityka">Polityka prywatności</a></footer>
</body></html>"""


LOREM = ("<p>To jest przykładowa treść komunikatu, wystarczająco długa, aby ekstraktor uznał ją za "
         "właściwy tekst artykułu, a nie za element nawigacji strony. Komunikat dotyczy ważnych spraw.</p>")


def pages() -> dict[str, tuple[str, str]]:
    p: dict[str, tuple[str, str]] = {}
    p["/robots.txt"] = ("text/plain", "User-agent: *\nDisallow: /private/\n\nUser-agent: BadBot\nDisallow: /\n")
    p["/feed.xml"] = ("application/rss+xml", """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"><channel><title>Policja test</title><link>http://127.0.0.1/</link>
<item><title>Zatrzymanie sprawcy kradzieży</title><link>/news/policja-1</link>
<pubDate>Sat, 21 Nov 2026 09:30:00 +0100</pubDate><description>Krótki opis</description></item>
<item><title>Tajny wpis</title><link>/private/tajne</link>
<pubDate>Sat, 21 Nov 2026 08:00:00 +0100</pubDate><description>Nie powinien zostać pobrany</description></item>
</channel></rss>""")
    p["/news/policja-1"] = ("text/html", article(
        "Zatrzymanie sprawcy kradzieży",
        "<p>Policjanci z Komendy Powiatowej Policji w Wołominie zatrzymali 34-letniego Jana Kowalskiego, "
        "PESEL 85010112345, podejrzanego o kradzież samochodu.</p>"
        "<p>Sprawę prowadzi asp. Anna Nowak z wydziału kryminalnego.</p>"
        "<p>W razie zagrożenia dzwoń pod numer alarmowy 112 lub na infolinię 800 120 226.</p>"
        + STATE["policja_extra"]))
    p["/private/tajne"] = ("text/html", article("Tajne", LOREM))
    for i in (1, 2, 3):
        p[f"/web/jednostka/art-{i}"] = ("text/html", article(f"Artykuł numer {i}", LOREM * 2))
    p["/web/jednostka/aktualnosci"] = ("text/html", """<!doctype html><html><head><meta charset="utf-8">
<title>Aktualności</title></head><body>
<header><nav class="menu"><ul>
  <li><a href="/web/jednostka/o-nas">O nas</a></li>
  <li><a href="/web/jednostka/kontakt">Kontakt</a></li>
  <li><a href="/web/jednostka/aktualnosci">Aktualności</a></li>
</ul></nav>
<div class="top"><div><a href="/web/jednostka/mapa-strony">Mapa strony</a></div>
<div class="banner">Ostatnia aktualizacja serwisu: 21.11.2026</div></div></header>
<main><p>Stan na 21.11.2026</p>
<ul class="art-list">
  <li><div class="tile"><a href="/web/jednostka/art-1">Artykuł numer 1</a><span class="date">21.11.2026</span></div></li>
  <li><div class="tile"><a href="/web/jednostka/art-2"><span class="event">20.11.2026 14:05</span> Artykuł numer 2</a></div></li>
  <li><div class="tile"><a href="/web/jednostka/art-3"><img alt=""></a><a href="/web/jednostka/art-3">Artykuł numer 3</a>
      <p class="d">19 listopada 2026</p></div></li>
  <li><div class="tile"><a href="/web/jednostka/zalacznik.pdf">Załącznik PDF</a> 18.11.2026</div></li>
  <li><div class="tile"><a href="http://example.org/web/jednostka/obcy">Obcy link</a> 18.11.2026</div></li>
</ul></main>
<footer><a href="/web/jednostka/deklaracja">Deklaracja dostępności</a> © 2026-11-21</footer>
</body></html>""")
    p["/sitemap.xml"] = ("application/xml", """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<sitemap><loc>{BASE}/sitemap-news.xml</loc><lastmod>2026-11-21</lastmod></sitemap>
</sitemapindex>""")
    p["/sitemap-news.xml"] = ("application/xml", """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<url><loc>{BASE}/news/sitemap-1</loc><lastmod>2026-11-20T12:00:00+01:00</lastmod></url>
<url><loc>{BASE}/news/sitemap-2</loc><lastmod>2026-11-21T12:00:00+01:00</lastmod></url>
<url><loc>https://umgdynia.dev.example.com/news/obca</loc><lastmod>2026-11-21</lastmod></url>
</urlset>""")
    p["/news/sitemap-1"] = ("text/html", article("Starszy komunikat z sitemapy", LOREM))
    p["/news/sitemap-2"] = ("text/html", article("Nowszy komunikat z sitemapy", LOREM))
    p["/api/warnings"] = ("application/json", json.dumps([
        {"id": "W1", "nazwa_zdarzenia": "Silny wiatr", "stopien": STATE["warning_level"],
         "opublikowano": "2026-11-21 08:00:00", "teryt": ["2201", "2202"], "tresc": "Prognozuje się silny wiatr."},
        {"id": "W2", "nazwa_zdarzenia": "Oblodzenie", "stopien": "1",
         "opublikowano": "2026-11-21 06:00:00", "teryt": ["1465"], "tresc": "Zamarzanie mokrej nawierzchni."},
    ], ensure_ascii=False))
    p["/static/info"] = ("text/html", article("Prawa pasażera – informacje", LOREM * 3))
    p["/js/page"] = ("text/html", """<!doctype html><html><head><meta charset="utf-8"><title>JS</title></head>
<body><div id="app">Ładowanie...</div>
<script>
setTimeout(function(){
  fetch('/api/js-content').then(r => r.json()).then(d => {
    document.getElementById('app').innerHTML = '<main><article><h1>' + d.title + '</h1><p>' + d.body + '</p></article></main>';
  });
}, 300);
</script></body></html>""")
    p["/js/robots-test"] = ("text/html", article("Strona próbująca pobrać zablokowany adres", LOREM * 2)
                            .replace("</body>", "<script>fetch('/private/js-data').catch(function(){});</script></body>"))
    p["/redirect-no-location"] = ("text/html", "")  # obsługiwane specjalnie w do_GET (302 bez Location)
    p["/api/js-content"] = ("application/json", json.dumps({
        "title": "Komunikat renderowany przez JavaScript",
        "body": "Ta treść pojawia się dopiero po wykonaniu skryptu na stronie. " * 4}, ensure_ascii=False))
    return p


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # cicho
        pass

    def _send(self, status: int, ctype: str = "text/plain", body: bytes = b"", headers: dict | None = None):
        self.send_response(status)
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if "charset" not in ctype else ""))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]
        ua = self.headers.get("User-Agent", "")
        if not ua.startswith("FaktBot/"):
            HITS[("bad-ua", path)] += 1
        if path == "/robots.txt" and self.headers.get("Host", "").startswith(ROBOTS_5XX_HOST):
            HITS[("robots-5xx", 500)] += 1
            return self._send(500, body=b"Internal Server Error")
        if self.headers.get("Host", "").startswith(ROBOTS_5XX_HOST):
            HITS[("after-robots-5xx", path)] += 1
        if path.startswith("/limited/"):
            HITS[(path, 429)] += 1
            return self._send(429, body=b"Too Many Requests", headers={"Retry-After": "120"})
        if path == "/redirect-no-location":
            HITS[(path, 302)] += 1
            return self._send(302, body=b"")
        pg = pages().get(path)
        if pg is None:
            HITS[(path, 404)] += 1
            return self._send(404, body=b"not found")
        ctype, body = pg
        base = f"http://{self.headers.get('Host')}"
        data = body.replace("{BASE}", base).encode("utf-8")
        etag = '"' + hashlib.md5(data).hexdigest() + '"'
        if self.headers.get("If-None-Match") == etag:
            HITS[(path, 304)] += 1
            return self._send(304, headers={"ETag": etag})
        HITS[(path, 200)] += 1
        self._send(200, ctype, data, {"ETag": etag})


class _ThreadingHTTPServerV6(ThreadingHTTPServer):
    address_family = socket.AF_INET6


def start(host: str = "127.0.0.1", port: int = 0) -> ThreadingHTTPServer:
    """Serwer w osobnym wątku; host z dwukropkiem (np. "::1") nasłuchuje po IPv6."""
    cls = _ThreadingHTTPServerV6 if ":" in host else ThreadingHTTPServer
    srv = cls((host, port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


if __name__ == "__main__":
    s = start(port=8765)
    print("Serwer testowy na http://127.0.0.1:8765 (Ctrl+C kończy)")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
