"""Test end-to-end bez internetu: lokalne serwery (conftest.py) + crawler uruchamiany przez CLI.

    uv run pytest backend/crawler
"""
from __future__ import annotations

import json
import os
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from crawler.adapters import Ctx, Stats, run_json_api
from crawler.config import SETTINGS_DEFAULTS
from crawler.fetcher import Fetcher, HttpError, Resp, RobotsUnavailable
from crawler.robots import parse_robots
from crawler.storage import Store

from . import local_server

OK_SOURCES = ("t-policja", "t-lista", "t-sitemap", "t-api", "t-static", "t-js", "t-js-robots")


def write_config(path: Path, base: str, limited: str, data: Path, robots5xx: str,
                 contact: str | None = "test@example.com") -> None:
    contact_line = f"  contact: {contact}\n" if contact else ""
    path.write_text(f"""
settings:
{contact_line}  data_dir: {data}
  min_delay_seconds: 0.05
defaults:
  interval_minutes: 1
sources:
  - name: t-policja
    type: rss
    institution: Komenda Testowa Policji
    urls: [{base}/feed.xml]
    recheck_hours: 0
  - name: t-lista
    type: html_list
    institution: Jednostka testowa
    url: {base}/web/jednostka/aktualnosci
    path_prefix: /web/jednostka/
  - name: t-sitemap
    type: sitemap
    institution: Test sitemap
    url: {base}/sitemap.xml
  - name: t-api
    type: json_api
    institution: Instytut Testowy
    endpoint: {base}/api/warnings
    id_field: id
    title_template: "{{nazwa_zdarzenia}} (stopień {{stopien}})"
    date_fields: [opublikowano]
    attribution: "Źródłem pochodzenia danych jest Instytut Testowy."
    doc_type: ostrzezenie
  - name: t-static
    type: static_page
    institution: Test static
    url: {base}/static/info
  - name: t-js
    type: static_page
    institution: Test JS
    url: {base}/js/page
  - name: t-js-robots
    type: static_page
    institution: Test JS robots
    url: {base}/js/robots-test
  - name: t-limited
    type: rss
    institution: Test 429
    url: {limited}/limited/feed.xml
  - name: t-robots5xx
    type: static_page
    institution: Test robots 5xx
    url: {robots5xx}/static/info
""", encoding="utf-8")


def crawler(cfg: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "crawler.cli", "--config", str(cfg), *args],
                          capture_output=True, text=True, timeout=300)


def docs(data: Path) -> list[dict]:
    p = data / "documents.jsonl"
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()] if p.exists() else []


def last_log(data: Path) -> dict[str, dict]:
    out = {}
    for l in (data / "crawl_log.jsonl").read_text(encoding="utf-8").splitlines():
        e = json.loads(l)
        out[e["source"]] = e
    return out


# ---------- 0. odmowy startu ----------
def test_refuses_start_without_contact(servers, tmp_path):
    cfg = tmp_path / "nomail.yaml"
    write_config(cfg, servers.base, servers.limited, tmp_path / "d0", servers.r5, contact=None)
    r = crawler(cfg, "once")
    assert r.returncode == 2 and "CRAWLER_CONTACT" in r.stderr, "brak maila kontaktowego → odmowa startu"


# ---------- 1. pierwszy przebieg ----------
@pytest.fixture(scope="module")
def first_run(servers, chromium, tmp_path_factory):
    """Pierwszy przebieg na świeżym katalogu; migawka dokumentów, logu i liczników serwera."""
    local_server.reset()
    tmp = tmp_path_factory.mktemp("faktbot")
    data, cfg = tmp / "data", tmp / "sources.yaml"
    write_config(cfg, servers.base, servers.limited, data, servers.r5)
    r = crawler(cfg, "once")
    D = docs(data)
    return SimpleNamespace(r=r, base=servers.base, data=data, cfg=cfg, D=D, by_url={d["url"]: d for d in D},
                           L=last_log(data) if (data / "crawl_log.jsonl").exists() else {}, hits=local_server.HITS.copy())


def test_first_run_exit_code(first_run):
    assert first_run.r.returncode == 0, f"crawler once kończy się kodem 0\n{first_run.r.stderr[-3000:]}"


def test_first_run_rss_and_robots(first_run):
    pol = first_run.by_url.get(f"{first_run.base}/news/policja-1")
    assert pol is not None, "RSS: artykuł policji pobrany (pełna treść)"
    assert "Stopka serwisu" not in pol["content"], "ekstrakcja: bez stopki"
    assert pol["fetch_method"] == "playwright", "fetch_method = playwright"
    assert pol["published_at"].startswith("2026-11-21T09:30"), "data z RSS"
    assert not any("/private/" in u for u in first_run.by_url), "robots.txt: /private/ nie pobrany"
    assert any("/private/tajne" in u for u in first_run.L["t-policja"].get("blocked_urls", [])), \
        "robots.txt: blokada zapisana w logu"
    assert first_run.hits[("/private/tajne", 200)] == 0, "serwer nie dostał żadnego żądania o /private/"


def test_first_run_html_list(first_run):
    base, by_url, L = first_run.base, first_run.by_url, first_run.L
    lista = {u for u in by_url if "/web/jednostka/" in u}
    assert lista == {f"{base}/web/jednostka/art-{i}" for i in (1, 2, 3)}, \
        f"html_list: dokładnie 3 kafelki (jest: {sorted(u.rsplit('/', 1)[1] for u in lista)})"
    assert L["t-lista"]["found"] == 3, "html_list: menu/stopka/PDF/obca domena odfiltrowane (found=3)"
    a2 = by_url.get(f"{base}/web/jednostka/art-2")
    assert bool(a2) and a2["published_at"].startswith("2026-11-20T14:05"), "html_list: data z tekstu linku z godziną"
    a3 = by_url.get(f"{base}/web/jednostka/art-3")
    assert bool(a3) and a3["published_at"].startswith("2026-11-19"), "html_list: data słowna (19 listopada 2026)"


def test_first_run_sitemap(first_run):
    base, by_url = first_run.base, first_run.by_url
    assert f"{base}/news/sitemap-1" in by_url and f"{base}/news/sitemap-2" in by_url, "sitemap: sitemapindex → 2 adresy"
    assert not any("dev.example.com" in u for u in by_url), "sitemap: obca domena zignorowana"
    sm = [d["url"] for d in first_run.D if d["source"] == "t-sitemap"]
    assert sm == [f"{base}/news/sitemap-2", f"{base}/news/sitemap-1"], "sitemap: najświeższe najpierw"


def test_first_run_json_api(first_run):
    api = [d for d in first_run.D if d["source"] == "t-api"]
    assert len(api) == 2 and {d["url"].split("#")[1] for d in api} == {"W1", "W2"}, "json_api: 2 rekordy, url=endpoint#id"
    w1 = next(d for d in api if d["url"].endswith("#W1"))
    assert w1["title"] == "Silny wiatr (stopień 2)", f"json_api: title_template ({w1['title']!r})"
    assert "stopien: 2" in w1["content"] and "teryt: 2201, 2202" in w1["content"], "json_api: spłaszczone pola"
    assert "Źródłem pochodzenia danych" in w1["content"] and w1.get("attribution"), "json_api: attribution"
    assert w1["published_at"].startswith("2026-11-21T08:00"), "json_api: date_fields"


def test_first_run_static_and_js_rendering(first_run):
    base, by_url, hits = first_run.base, first_run.by_url, first_run.hits
    assert f"{base}/static/info" in by_url, "static_page zapisana"
    js = by_url.get(f"{base}/js/page")
    assert bool(js) and "renderowany przez JavaScript" in (js["title"] or "") and "po wykonaniu skryptu" in js["content"], \
        "Playwright: treść ładowana przez JS wyrenderowana"
    assert hits[("/js/robots-test", 200)] == 1 and not any(k[0] == "/private/js-data" for k in hits), \
        "Playwright: skrypt strony nie pobrał adresu zablokowanego w robots.txt"


def test_first_run_politeness(first_run):
    L, hits = first_run.L, first_run.hits
    assert L["t-limited"]["status"] == "throttled", f"429 → throttled (jest {L['t-limited']['status']})"
    assert hits[("/limited/feed.xml", 429)] == 1, "429: dokładnie jedno żądanie (bez ponawiania)"
    assert sum(v for (p, _), v in hits.items() if p == "bad-ua") == 0, "każde żądanie z UA FaktBot/…"
    assert L["t-robots5xx"]["status"] == "throttled" and hits[("robots-5xx", 500)] == 1 \
        and not any(k[0] == "after-robots-5xx" for k in hits), \
        f"robots.txt 5xx → nic nie pobrano z hosta ({L['t-robots5xx']['status']})"


@pytest.mark.parametrize("name", OK_SOURCES)
def test_first_run_source_status(first_run, name):
    e = first_run.L[name]
    assert e["status"] == "ok", f"{name}: status ok (jest {e['status']}: {e['message']})"


# ---------- 2–4. kolejne przebiegi na tym samym katalogu danych (kolejność w jednym teście) ----------
def test_rerun_content_change_and_status(first_run, fresh_server):
    base, data, cfg, HITS = first_run.base, first_run.data, first_run.cfg, local_server.HITS

    # [2] drugi przebieg – nic nie powinno być pobrane ponownie
    hits_before = HITS.copy()
    r = crawler(cfg, "once")
    assert r.returncode == 0, "kod 0"
    D2 = docs(data)
    assert len(D2) == len(first_run.D), f"brak nowych linii w documents.jsonl ({len(D2)} vs {len(first_run.D)})"
    L2 = last_log(data)
    for name in ("t-policja", "t-lista", "t-sitemap", "t-api", "t-static", "t-js"):
        e = L2[name]
        assert e["new"] == 0 and e["updated"] == 0 and e["status"] in ("ok", "not_modified"), \
            f"{name}: {e['status']} (new={e['new']}, updated={e['updated']})"
    re_fetched = [k[0] for k, v in HITS.items()
                  if k[1] == 200 and v > hits_before.get(k, 0)
                  and k[0].startswith(("/news/", "/web/jednostka/art", "/static", "/js/"))]
    assert not re_fetched, f"artykuły nie pobrane ponownie z kodem 200 (ponownie: {re_fetched})"
    assert HITS[("/news/policja-1", 304)] > hits_before[("/news/policja-1", 304)], \
        "recheck_hours=0 → zapytanie warunkowe → 304"
    assert L2["t-limited"]["status"] == "throttled" \
        and HITS[("/limited/feed.xml", 429)] == hits_before[("/limited/feed.xml", 429)], \
        "wycofanie trwa: brak nowego żądania do hosta z 429"

    # [3] zmiana treści artykułu i pola JSON → nowe wersje
    local_server.STATE["policja_extra"] = "<p>Aktualizacja: zatrzymany usłyszał zarzuty i został tymczasowo aresztowany.</p>"
    local_server.STATE["warning_level"] = "3"
    r = crawler(cfg, "once", "--source", "t-policja", "--source", "t-api")
    assert r.returncode == 0, "kod 0"
    D3 = docs(data)
    assert D3[:len(D2)] == D2, "stare linie documents.jsonl nietknięte"
    pol_v = [d for d in D3 if d["url"] == f"{base}/news/policja-1"]
    assert [d["version"] for d in pol_v] == [1, 2], f"artykuł: wersje 1 i 2 ({[d['version'] for d in pol_v]})"
    assert pol_v[1]["id"].endswith("-v2") and "aresztowany" in pol_v[1]["content"], "v2 zawiera poprawkę"
    w1v = [d for d in D3 if d["url"].endswith("#W1")]
    assert [d["version"] for d in w1v] == [1, 2] and "stopien: 3" in w1v[-1]["content"], "JSON: zmiana pola → wersja 2"
    w2v = [d for d in D3 if d["url"].endswith("#W2")]
    assert len(w2v) == 1, "JSON: niezmieniony rekord bez nowej wersji"
    cur = json.loads((data / "current.json").read_text())
    assert cur[f"{base}/news/policja-1"]["version"] == 2, "current.json wskazuje v2"

    # [4] status
    r = crawler(cfg, "status")
    assert r.returncode == 0 and "THROTTLED" in r.stdout and "Wersje" in r.stdout, \
        f"status wypisuje tabelę z alarmami\n{r.stdout}"


# ---------- 5. tryb ciągły + SIGTERM ----------
def test_run_stops_on_sigterm(fresh_server, tmp_path):
    data = tmp_path / "data5"
    cfg = tmp_path / "run.yaml"
    write_config(cfg, fresh_server.base, fresh_server.limited, data, fresh_server.r5)
    p = subprocess.Popen([sys.executable, "-m", "crawler.cli", "--config", str(cfg), "run"],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    deadline = time.time() + 120
    while time.time() < deadline and not (data / "crawl_log.jsonl").exists():
        time.sleep(0.5)
    time.sleep(1)
    p.send_signal(signal.SIGTERM)
    out, _ = p.communicate(timeout=120)
    n_runs = len((data / "crawl_log.jsonl").read_text().splitlines())
    assert p.returncode == 0 and "kończę po bieżącym źródle" in out and "Koniec pracy" in out, \
        f"SIGTERM kończy pracę po bieżącym źródle (przebiegów: {n_runs}, kod {p.returncode})"
    st = json.loads((data / "state.json").read_text())
    assert all(v.get("last_run") for k, v in st["sources"].items()), "stan zapisany dla uruchomionych źródeł"


# ---------- 6. testy jednostkowe (bez sieci zewnętrznej) ----------
def test_robots_user_agent_matching():
    r = parse_robots("User-agent:\nDisallow: /\n\nUser-agent: *\nDisallow: /tajne/\n", "FaktBot")
    assert r.allowed("https://x.pl/strona") and not r.allowed("https://x.pl/tajne/a"), \
        "robots.txt: pusty User-agent nie dotyczy FaktBota (dokładny token, RFC 9309)"
    r = parse_robots("User-agent: FaktBot/1.0\nDisallow: /a\n", "FaktBot")
    assert not r.allowed("https://x.pl/a"), "robots.txt: grupa 'FaktBot/1.0' pasuje do FaktBota"


@pytest.fixture
def fetcher(tmp_path):
    f = Fetcher(dict(SETTINGS_DEFAULTS, min_delay_seconds=0.05), Store(tmp_path / "u1"), "FaktBot/1.0 (+t@example.com)")
    yield f
    f.close()


def test_robots_5xx_cached_unavailable(fetcher):
    resp500 = mock.Mock(status_code=500, text="", headers={})
    with mock.patch.object(fetcher, "_raw_get", return_value=resp500):
        outcomes = []
        for _ in range(2):
            try:
                fetcher.check_allowed("https://example.invalid/a")
                outcomes.append("ok")
            except RobotsUnavailable:
                outcomes.append("unavailable")
            except Exception as e:
                outcomes.append(type(e).__name__)
    assert outcomes == ["unavailable", "unavailable"], \
        f"robots.txt 5xx: także z pamięci podręcznej → RobotsUnavailable ({outcomes})"


def test_robots_redirect_loop(fetcher):
    with mock.patch.object(fetcher, "_raw_get", side_effect=HttpError("Za dużo przekierowań")):
        with pytest.raises(RobotsUnavailable):  # robots.txt: pętla przekierowań → RobotsUnavailable
            fetcher.robots_for("https://petla.invalid/x")


def test_redirect_without_location(fetcher, servers):
    with pytest.raises(HttpError):  # 3xx bez Location → HttpError (nie sukces)
        fetcher.http_get(servers.base + "/redirect-no-location", conditional=False)


def test_json_api_duplicate_ids_and_current_json_mode(tmp_path):
    store = Store(tmp_path / "u2")

    class FakeFetcher:
        def http_get(self, url, conditional=True, accept=None):
            body = json.dumps([{"id": "A", "v": 1}, {"id": "A", "v": 2}, {"id": "B", "v": 3}])
            return Resp(url=url, final_url=url, status=200, text=body, headers={"content-type": "application/json"})

        def commit_validators(self, resp):
            pass

    ctx = Ctx(store=store, fetcher=FakeFetcher())
    src = {"name": "u", "institution": "X", "urls": ["https://api.invalid/w"], "id_field": "id"}
    for _ in range(2):
        st, stats = store.source_state("u"), Stats()
        run_json_api(ctx, src, st, stats)
    urls = sorted(store.current)
    assert urls == ["https://api.invalid/w#A", "https://api.invalid/w#A~2", "https://api.invalid/w#B"] \
        and store.count_versions() == 3, \
        f"json_api: powtórzony id → osobny dokument, bez przeplatania wersji ({urls}, wersji {store.count_versions()})"
    mode = stat.S_IMODE(os.stat(store.current_path).st_mode)
    assert mode == 0o644, f"current.json zapisany z uprawnieniami 0644 (jest {oct(mode)})"


def test_json_api_pages_filter_details(tmp_path):
    """Jak RSO / GIOŚ: stronicowanie, record_filter, detail_url, drop_fields, require_fields."""
    pages = {
        "https://api.invalid/l/1": {"items": [{"id": 1, "miasto": "Toruń", "f": "x"}, {"id": 2, "miasto": "Gdynia"}]},
        "https://api.invalid/l/2": {"items": [{"id": 3, "miasto": "toruń"}, {"id": 4, "miasto": "Toruń"}]},
        "https://api.invalid/l/3": {"items": [{"id": 5, "miasto": "Toruń"}, {"id": 6, "miasto": "Gdynia"}]},
        # jak RSO: numer strony spoza zakresu zwraca znów ostatnią stronę
        "https://api.invalid/l/4": {"items": [{"id": 5, "miasto": "Toruń"}, {"id": 6, "miasto": "Gdynia"}]},
    }
    asked: list[tuple[str, bool]] = []

    class PagedFetcher:
        def http_get(self, url, conditional=True, accept=None):
            asked.append((url, conditional))
            if "/d/" in url:
                n = int(url.rsplit("/", 1)[1])
                body = {"Indeks": {"wartosc": n * 10 if n != 4 else None, "smiec": "z"}}  # 4: brak indeksu
            else:
                body = pages[url]
            return Resp(url=url, final_url=url, status=200, text=json.dumps(body), headers={})

        def commit_validators(self, resp):
            pass

    store = Store(tmp_path / "u4")
    src = {"name": "p", "institution": "X", "urls": ["https://api.invalid/l/1"],
           "page_template": "https://api.invalid/l/{page}", "page_start": 1, "records_path": "items",
           "record_filter": {"miasto": ["Toruń"]}, "detail_url": "https://api.invalid/d/{id}",
           "detail_path": "Indeks", "detail_as": "ix", "drop_fields": ["ix.smiec", "f"],
           "require_fields": ["ix.wartosc"], "id_template": "{id}", "title_template": "Stacja {id}: {ix.wartosc}"}
    stats = Stats()
    run_json_api(Ctx(store=store, fetcher=PagedFetcher()), src, store.source_state("p"), stats)
    by_url = {d["url"]: d for d in docs(tmp_path / "u4")}
    assert sorted(by_url) == [f"https://api.invalid/d/{i}#{i}" for i in (1, 3, 5)], \
        f"json_api: 3 strony, filtr bez wielkości liter, require_fields, adres = detail_url#id ({sorted(by_url)})"
    assert stats.skipped == 1, "json_api: rekord bez wymaganego pola pominięty"
    d1 = by_url["https://api.invalid/d/1#1"]
    assert d1["title"] == "Stacja 1: 10" and "ix.wartosc: 10" in d1["content"], "json_api: szczegóły pod detail_as"
    assert "smiec" not in d1["content"] and "f: x" not in d1["content"], "json_api: drop_fields usuwa pola"
    assert all(not c for _, c in asked), "json_api: stronicowanie i szczegóły bez zapytań warunkowych"
    assert "https://api.invalid/l/5" not in [u for u, _ in asked], "json_api: strona bez nowych rekordów kończy"


def test_json_api_token_pagination(tmp_path):
    """Jak Google Fact Check: nextPageToken → kolejna strona z ?pageToken=, klucz tylko w nagłówku."""
    asked = []

    class TokenFetcher:
        def http_get(self, url, conditional=True, accept=None, private=None):
            asked.append((url, conditional, private))
            if "pageToken=T2" in url:
                body = {"claims": [{"id": 3}]}  # ostatnia strona: bez nextPageToken
            elif "pageToken=T1" in url:
                body = {"claims": [{"id": 2}], "nextPageToken": "T2"}
            else:
                body = {"claims": [{"id": 1}], "nextPageToken": "T1"}
            return Resp(url=url, final_url=url, status=200, text=json.dumps(body), headers={})

        def commit_validators(self, resp):
            pass

    store = Store(tmp_path / "tok")
    src = {"name": "t", "institution": "X", "urls": ["https://api.invalid/claims?q=pl&pageSize=1"],
           "records_path": "claims", "id_field": "id", "next_token_path": "nextPageToken",
           "token_param": "pageToken"}
    run_json_api(Ctx(store=store, fetcher=TokenFetcher()), src, store.source_state("t"), Stats())
    assert sorted(d["url"].split("#")[1] for d in docs(tmp_path / "tok")) == ["1", "2", "3"], "3 strony przez token"
    assert [u for u, _, _ in asked][1] == "https://api.invalid/claims?q=pl&pageSize=1&pageToken=T1"
    assert not any(c for _, c, _ in asked), "stronicowanie tokenem bez zapytań warunkowych"
    stats = Stats()
    asked.clear()
    run_json_api(Ctx(store=store, fetcher=TokenFetcher()), {**src, "max_pages": 2}, store.source_state("t"), stats)
    assert len(asked) == 2 and any("max_pages" in m for m in stats.messages), "max_pages ogranicza liczbę stron"


def test_scheduler_background_sources_wait():
    """Źródła background (mapy stron gov.pl) nie zagładzają zwykłych, nawet gdy są bardziej zaległe."""
    from datetime import datetime, timedelta, timezone
    from crawler.runner import pick_next
    now = datetime(2026, 11, 22, 12, tzinfo=timezone.utc)
    bg = {"name": "mapa", "background": True}
    alert = {"name": "alert"}
    sched = [(now - timedelta(hours=5), "mapa", bg), (now - timedelta(minutes=1), "alert", alert)]
    assert pick_next(sched, now) is alert, "zwykłe źródło przed zaległym background"
    sched = [(now - timedelta(hours=5), "mapa", bg), (now + timedelta(minutes=4), "alert", alert)]
    assert pick_next(sched, now) is bg, "background, gdy nic zwykłego nie czeka"
    assert pick_next([(now + timedelta(minutes=1), "alert", alert)], now) is None


def test_readonly_store_writes_nothing(tmp_path):
    ro_dir = tmp_path / "u3"
    ro_dir.mkdir()
    (ro_dir / "documents.jsonl").write_text(json.dumps({"url": "u", "version": 1, "content_hash": "h", "id": "x-v1"}) + "\n")
    Store(ro_dir, readonly=True)
    assert not (ro_dir / "current.json").exists(), "status (Store readonly) niczego nie zapisuje"


def test_private_headers_stay_on_source_host(fetcher):
    """Klucz API (private) idzie do hosta źródła, ale nie po przekierowaniu na inny host."""
    sent = []

    def fake_get(url, headers):
        sent.append((url, dict(headers)))
        if "a.invalid" in url:
            return mock.Mock(status_code=302, headers={"location": "https://b.invalid/x"}, url=url)
        return mock.Mock(status_code=200, headers={}, url=url, content=b"{}")

    with mock.patch.object(fetcher.client, "get", side_effect=fake_get):
        fetcher._raw_get("https://a.invalid/api", {"Accept": "application/json"}, check_robots=False,
                         private={"X-Goog-Api-Key": "SEKRET"})
    assert sent[0][1].get("X-Goog-Api-Key") == "SEKRET", "klucz wysłany do hosta źródła"
    assert "X-Goog-Api-Key" not in sent[1][1] and sent[1][1].get("Accept"), "klucz nie idzie na inny host"


def test_json_api_env_headers_and_url_template(tmp_path, monkeypatch):
    seen_private = []

    class KeyFetcher:
        def http_get(self, url, conditional=True, accept=None, private=None):
            seen_private.append(private)
            review = {"url": "https://fc.invalid/weryfikacja-1", "title": "Fałsz", "reviewDate": "2026-11-20T10:00:00Z"}
            body = {"claims": [{"text": "Twierdzenie", "claimReview": [review]},
                               {"text": "Drugie twierdzenie z tej samej weryfikacji", "claimReview": [review]}]}
            return Resp(url=url, final_url=url, status=200, text=json.dumps(body), headers={})

        def commit_validators(self, resp):
            pass

    src = {"name": "fc", "institution": "X", "urls": ["https://api.invalid/claims"], "records_path": "claims",
           "headers": {"X-Api-Key": "${TEST_FC_KEY}"}, "url_template": "{claimReview[0].url}",
           "id_template": "{claimReview[0].url}", "title_template": "{claimReview[0].title}",
           "date_fields": ["claimReview[0].reviewDate"]}
    store = Store(tmp_path / "fc")
    monkeypatch.delenv("TEST_FC_KEY", raising=False)
    with pytest.raises(ValueError, match="TEST_FC_KEY"):  # brak zmiennej: błąd źródła, bez wartości w komunikacie
        run_json_api(Ctx(store=store, fetcher=KeyFetcher()), src, store.source_state("fc"), Stats())
    monkeypatch.setenv("TEST_FC_KEY", "SEKRET")
    run_json_api(Ctx(store=store, fetcher=KeyFetcher()), src, store.source_state("fc"), Stats())
    d, d2 = docs(tmp_path / "fc")
    assert seen_private[-1] == {"X-Api-Key": "SEKRET"}, "nagłówek z wartością ze środowiska"
    assert d["url"] == "https://fc.invalid/weryfikacja-1" and d["title"] == "Fałsz", "url_template jako adres dokumentu"
    assert d2["url"] == "https://fc.invalid/weryfikacja-1#2" and d2["version"] == 1, \
        "powtórzony adres z url_template: osobny dokument, a nie nowa wersja"
    assert "SEKRET" not in json.dumps(d), "klucz nie trafia do dokumentu"
    with pytest.raises(ValueError, match="User-Agent"):
        run_json_api(Ctx(store=store, fetcher=KeyFetcher()), {**src, "headers": {"User-Agent": "x"}},
                     store.source_state("fc"), Stats())


def test_parse_compact_date():
    from crawler.extract import parse_any_date
    assert parse_any_date("20261120160042").isoformat() == "2026-11-20T16:00:42+01:00"
