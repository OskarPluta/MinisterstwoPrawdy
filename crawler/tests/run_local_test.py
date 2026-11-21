#!/usr/bin/env python3
"""Test end-to-end bez internetu: lokalny serwer + crawler uruchamiany przez CLI.

    python tests/run_local_test.py
"""
from __future__ import annotations

import json
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
import local_server  # noqa: E402

FAILS: list[str] = []


def check(cond: bool, label: str) -> None:
    print(("  ✔ " if cond else "  ✘ ") + label)
    if not cond:
        FAILS.append(label)


def write_config(path: Path, base: str, limited: str, data: Path, extra_settings: str = "",
                 robots5xx: str = "http://127.0.0.3:1",
                 contact: str | None = "test@example.com") -> None:
    contact_line = f"  contact: {contact}\n" if contact else ""
    path.write_text(f"""
settings:
{contact_line}  data_dir: {data}
  min_delay_seconds: 0.05
{extra_settings}
defaults:
  interval_minutes: 1
sources:
  - name: t-policja
    type: rss
    institution: Komenda Testowa Policji
    urls: [{base}/feed.xml]
    mask_pii: true
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
    return subprocess.run([sys.executable, str(ROOT / "crawler.py"), "--config", str(cfg), *args],
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


def unit_checks(base: str, tmp: Path) -> None:
    import os
    import stat
    from unittest import mock

    sys.path.insert(0, str(ROOT))
    from faktbot.adapters import Ctx, Stats, run_json_api
    from faktbot.config import SETTINGS_DEFAULTS
    from faktbot.fetcher import Fetcher, HttpError, Resp, RobotsUnavailable
    from faktbot.robots import parse_robots
    from faktbot.storage import Store

    r = parse_robots("User-agent:\nDisallow: /\n\nUser-agent: *\nDisallow: /tajne/\n", "FaktBot")
    check(r.allowed("https://x.pl/strona") and not r.allowed("https://x.pl/tajne/a"),
          "robots.txt: pusty User-agent nie dotyczy FaktBota (dokładny token, RFC 9309)")
    r = parse_robots("User-agent: FaktBot/1.0\nDisallow: /a\n", "FaktBot")
    check(not r.allowed("https://x.pl/a"), "robots.txt: grupa 'FaktBot/1.0' pasuje do FaktBota")

    settings = dict(SETTINGS_DEFAULTS, min_delay_seconds=0.05)
    f = Fetcher(settings, Store(tmp / "u1"), "FaktBot/1.0 (+t@example.com)")
    resp500 = mock.Mock(status_code=500, text="", headers={})
    with mock.patch.object(f, "_raw_get", return_value=resp500):
        outcomes = []
        for _ in range(2):
            try:
                f.check_allowed("https://example.invalid/a")
                outcomes.append("ok")
            except RobotsUnavailable:
                outcomes.append("unavailable")
            except Exception as e:
                outcomes.append(type(e).__name__)
    check(outcomes == ["unavailable", "unavailable"],
          f"robots.txt 5xx: także z pamięci podręcznej → RobotsUnavailable ({outcomes})")
    with mock.patch.object(f, "_raw_get", side_effect=HttpError("Za dużo przekierowań")):
        try:
            f.robots_for("https://petla.invalid/x")
            ok = False
        except RobotsUnavailable:
            ok = True
    check(ok, "robots.txt: pętla przekierowań → RobotsUnavailable")
    try:
        f.http_get(base + "/redirect-no-location", conditional=False)
        ok = False
    except HttpError:
        ok = True
    check(ok, "3xx bez Location → HttpError (nie sukces)")
    f.close()

    store = Store(tmp / "u2")

    class FakeFetcher:
        def http_get(self, url, conditional=True, accept=None):
            body = json.dumps([{"id": "A", "v": 1}, {"id": "A", "v": 2}, {"id": "B", "v": 3}])
            return Resp(url=url, final_url=url, status=200, text=body, headers={"content-type": "application/json"})

        def commit_validators(self, resp):
            pass

    ctx = Ctx(store=store, fetcher=FakeFetcher(), masker=None)
    src = {"name": "u", "institution": "X", "urls": ["https://api.invalid/w"], "id_field": "id"}
    for _ in range(2):
        st, stats = store.source_state("u"), Stats()
        run_json_api(ctx, src, st, stats)
    urls = sorted(store.current)
    check(urls == ["https://api.invalid/w#A", "https://api.invalid/w#A~2", "https://api.invalid/w#B"]
          and store.count_versions() == 3,
          f"json_api: powtórzony id → osobny dokument, bez przeplatania wersji ({urls}, wersji {store.count_versions()})")
    mode = stat.S_IMODE(os.stat(store.current_path).st_mode)
    check(mode == 0o644, f"current.json zapisany z uprawnieniami 0644 (jest {oct(mode)})")

    ro_dir = tmp / "u3"
    ro_dir.mkdir()
    (ro_dir / "documents.jsonl").write_text(json.dumps({"url": "u", "version": 1, "content_hash": "h", "id": "x-v1"}) + "\n")
    Store(ro_dir, readonly=True)
    check(not (ro_dir / "current.json").exists(), "status (Store readonly) niczego nie zapisuje")


def main() -> int:
    srv = local_server.start("127.0.0.1")
    srv2 = local_server.start("127.0.0.2")
    srv3 = local_server.start("127.0.0.3")
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    limited = f"http://127.0.0.2:{srv2.server_address[1]}"
    r5 = f"http://127.0.0.3:{srv3.server_address[1]}"
    tmp = Path(tempfile.mkdtemp(prefix="faktbot-test-"))
    data = tmp / "data"
    cfg = tmp / "sources.yaml"
    write_config(cfg, base, limited, data, robots5xx=r5)
    print(f"Serwer: {base}  (429: {limited})   dane: {data}")

    # ---------- 0. odmowy startu ----------
    print("\n[0] Odmowy startu")
    cfg_nomail = tmp / "nomail.yaml"
    write_config(cfg_nomail, base, limited, tmp / "d0", contact=None)
    r = crawler(cfg_nomail, "once")
    check(r.returncode == 2 and "CRAWLER_CONTACT" in r.stderr, "brak maila kontaktowego → odmowa startu")
    cfg_nomodel = tmp / "nomodel.yaml"
    write_config(cfg_nomodel, base, limited, tmp / "d1", extra_settings="  spacy_models: [nie_ma_takiego_modelu]")
    r = crawler(cfg_nomodel, "once")
    check(r.returncode == 2 and "maskowanie" in r.stderr.lower(), "brak modelu spaCy + mask_pii → odmowa startu")
    check(not (tmp / "d1" / "documents.jsonl").exists(), "…i nic nie zapisano")

    # ---------- 1. pierwszy przebieg ----------
    print("\n[1] Pierwszy przebieg")
    r = crawler(cfg, "once")
    if r.returncode != 0:
        print(r.stderr[-3000:])
    check(r.returncode == 0, "crawler.py once kończy się kodem 0")
    D = docs(data)
    by_url = {d["url"]: d for d in D}
    L = last_log(data)

    pol = by_url.get(f"{base}/news/policja-1")
    check(pol is not None, "RSS: artykuł policji pobrany (pełna treść)")
    if pol:
        check("Kowalsk" not in pol["content"] and "Nowak" not in pol["content"], "PII: nazwiska zamaskowane")
        check("[OSOBA]" in pol["content"], "PII: wstawiono [OSOBA]")
        check("85010112345" not in pol["content"] and "[PESEL]" in pol["content"], "PII: PESEL → [PESEL]")
        check("112" in pol["content"] and "800 120 226" in pol["content"], "PII: numery alarmowe/infolinie zostają")
        check("Stopka serwisu" not in pol["content"], "ekstrakcja: bez stopki")
        check(pol["fetch_method"] == "playwright", "fetch_method = playwright")
        check(pol["published_at"].startswith("2026-11-21T09:30"), "data z RSS")
    check(not any("/private/" in u for u in by_url), "robots.txt: /private/ nie pobrany")
    check(any("/private/tajne" in u for u in L["t-policja"].get("blocked_urls", [])), "robots.txt: blokada zapisana w logu")
    check(local_server.HITS[("/private/tajne", 200)] == 0, "serwer nie dostał żadnego żądania o /private/")

    lista = {u for u in by_url if "/web/jednostka/" in u}
    check(lista == {f"{base}/web/jednostka/art-{i}" for i in (1, 2, 3)},
          f"html_list: dokładnie 3 kafelki (jest: {sorted(u.rsplit('/', 1)[1] for u in lista)})")
    check(L["t-lista"]["found"] == 3, "html_list: menu/stopka/PDF/obca domena odfiltrowane (found=3)")
    a2 = by_url.get(f"{base}/web/jednostka/art-2")
    check(bool(a2) and a2["published_at"].startswith("2026-11-20T14:05"), "html_list: data z tekstu linku z godziną")
    a3 = by_url.get(f"{base}/web/jednostka/art-3")
    check(bool(a3) and a3["published_at"].startswith("2026-11-19"), "html_list: data słowna (19 listopada 2026)")

    check(f"{base}/news/sitemap-1" in by_url and f"{base}/news/sitemap-2" in by_url, "sitemap: sitemapindex → 2 adresy")
    check(not any("dev.example.com" in u for u in by_url), "sitemap: obca domena zignorowana")
    sm = [d["url"] for d in D if d["source"] == "t-sitemap"]
    check(sm == [f"{base}/news/sitemap-2", f"{base}/news/sitemap-1"], "sitemap: najświeższe najpierw")

    api = [d for d in D if d["source"] == "t-api"]
    check(len(api) == 2 and {d["url"].split("#")[1] for d in api} == {"W1", "W2"}, "json_api: 2 rekordy, url=endpoint#id")
    w1 = next((d for d in api if d["url"].endswith("#W1")), None)
    if w1:
        check(w1["title"] == "Silny wiatr (stopień 2)", f"json_api: title_template ({w1['title']!r})")
        check("stopien: 2" in w1["content"] and "teryt: 2201, 2202" in w1["content"], "json_api: spłaszczone pola")
        check("Źródłem pochodzenia danych" in w1["content"] and w1.get("attribution"), "json_api: attribution")
        check(w1["published_at"].startswith("2026-11-21T08:00"), "json_api: date_fields")

    check(f"{base}/static/info" in by_url, "static_page zapisana")
    js = by_url.get(f"{base}/js/page")
    check(bool(js) and "renderowany przez JavaScript" in (js["title"] or "") and "po wykonaniu skryptu" in js["content"],
          "Playwright: treść ładowana przez JS wyrenderowana")
    check(local_server.HITS[("/js/robots-test", 200)] == 1 and
          not any(k[0] == "/private/js-data" for k in local_server.HITS),
          "Playwright: skrypt strony nie pobrał adresu zablokowanego w robots.txt")
    check(L["t-limited"]["status"] == "throttled", f"429 → throttled (jest {L['t-limited']['status']})")
    check(local_server.HITS[("/limited/feed.xml", 429)] == 1, "429: dokładnie jedno żądanie (bez ponawiania)")
    check(sum(v for (p, _), v in local_server.HITS.items() if p == "bad-ua") == 0, "każde żądanie z UA FaktBot/…")
    check(L["t-robots5xx"]["status"] == "throttled" and local_server.HITS[("robots-5xx", 500)] == 1
          and not any(k[0] == "after-robots-5xx" for k in local_server.HITS),
          f"robots.txt 5xx → nic nie pobrano z hosta ({L['t-robots5xx']['status']})")
    for name in ("t-policja", "t-lista", "t-sitemap", "t-api", "t-static", "t-js", "t-js-robots"):
        check(L[name]["status"] == "ok", f"{name}: status ok (jest {L[name]['status']}: {L[name]['message']})")

    # ---------- 2. drugi przebieg: nic nowego ----------
    print("\n[2] Drugi przebieg – nic nie powinno być pobrane ponownie")
    hits_200_before = {k: v for k, v in local_server.HITS.items() if k[1] == 200}
    n_before = len(D)
    r = crawler(cfg, "once")
    check(r.returncode == 0, "kod 0")
    D2 = docs(data)
    check(len(D2) == n_before, f"brak nowych linii w documents.jsonl ({len(D2)} vs {n_before})")
    L2 = last_log(data)
    for name in ("t-policja", "t-lista", "t-sitemap", "t-api", "t-static", "t-js"):
        e = L2[name]
        check(e["new"] == 0 and e["updated"] == 0 and e["status"] in ("ok", "not_modified"),
              f"{name}: {e['status']} (new={e['new']}, updated={e['updated']})")
    re_fetched = [k[0] for k, v in local_server.HITS.items()
                  if k[1] == 200 and v > hits_200_before.get(k, 0) and k[0].startswith(("/news/", "/web/jednostka/art", "/static", "/js/"))]
    check(not re_fetched, f"artykuły nie pobrane ponownie z kodem 200 (ponownie: {re_fetched})")
    check(local_server.HITS[("/news/policja-1", 304)] >= 1, "recheck_hours=0 → zapytanie warunkowe → 304")
    check(L2["t-limited"]["status"] == "throttled" and local_server.HITS[("/limited/feed.xml", 429)] == 1,
          "wycofanie trwa: brak nowego żądania do hosta z 429")

    # ---------- 3. zmiana treści ----------
    print("\n[3] Zmiana treści artykułu i pola JSON → nowe wersje")
    local_server.STATE["policja_extra"] = "<p>Aktualizacja: zatrzymany usłyszał zarzuty i został tymczasowo aresztowany.</p>"
    local_server.STATE["warning_level"] = "3"
    r = crawler(cfg, "once", "--source", "t-policja", "--source", "t-api")
    check(r.returncode == 0, "kod 0")
    D3 = docs(data)
    check(D3[:len(D2)] == D2, "stare linie documents.jsonl nietknięte")
    pol_v = [d for d in D3 if d["url"] == f"{base}/news/policja-1"]
    check([d["version"] for d in pol_v] == [1, 2], f"artykuł: wersje 1 i 2 ({[d['version'] for d in pol_v]})")
    if len(pol_v) == 2:
        check(pol_v[1]["id"].endswith("-v2") and "aresztowany" in pol_v[1]["content"], "v2 zawiera poprawkę")
        check("Kowalsk" not in pol_v[1]["content"], "v2 też zamaskowana")
    w1v = [d for d in D3 if d["url"].endswith("#W1")]
    check([d["version"] for d in w1v] == [1, 2] and "stopien: 3" in w1v[-1]["content"], "JSON: zmiana pola → wersja 2")
    w2v = [d for d in D3 if d["url"].endswith("#W2")]
    check(len(w2v) == 1, "JSON: niezmieniony rekord bez nowej wersji")
    cur = json.loads((data / "current.json").read_text())
    check(cur[f"{base}/news/policja-1"]["version"] == 2, "current.json wskazuje v2")

    # ---------- 4. status ----------
    print("\n[4] status")
    r = crawler(cfg, "status")
    print("\n".join("    " + l for l in r.stdout.splitlines()))
    check(r.returncode == 0 and "THROTTLED" in r.stdout and "Wersje" in r.stdout, "status wypisuje tabelę z alarmami")

    # ---------- 5. tryb ciągły + SIGTERM ----------
    print("\n[5] run + SIGTERM")
    data5 = tmp / "data5"
    cfg5 = tmp / "run.yaml"
    write_config(cfg5, base, limited, data5, robots5xx=r5)
    p = subprocess.Popen([sys.executable, str(ROOT / "crawler.py"), "--config", str(cfg5), "run"],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    deadline = time.time() + 120
    while time.time() < deadline and not (data5 / "crawl_log.jsonl").exists():
        time.sleep(0.5)
    time.sleep(1)
    p.send_signal(signal.SIGTERM)
    out, _ = p.communicate(timeout=120)
    n_runs = len((data5 / "crawl_log.jsonl").read_text().splitlines())
    check(p.returncode == 0 and "kończę po bieżącym źródle" in out and "Koniec pracy" in out,
          f"SIGTERM kończy pracę po bieżącym źródle (przebiegów: {n_runs}, kod {p.returncode})")
    st = json.loads((data5 / "state.json").read_text())
    check(all(v.get("last_run") for k, v in st["sources"].items()), "stan zapisany dla uruchomionych źródeł")

    # ---------- 6. testy jednostkowe (bez sieci zewnętrznej) ----------
    print("\n[6] Testy jednostkowe")
    unit_checks(base, tmp)

    srv.shutdown()
    srv2.shutdown()
    srv3.shutdown()
    print(f"\n{'WSZYSTKO OK' if not FAILS else f'{len(FAILS)} BŁĘDÓW'}  (dane testowe: {data})")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
