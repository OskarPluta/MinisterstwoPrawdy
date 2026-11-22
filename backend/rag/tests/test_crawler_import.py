"""crawler_import.convert: crawler output -> corpus JSON (no database needed)."""

import json
from pathlib import Path

import pytest

from rag.chunking import chunk_record
from rag.crawler_import import convert

DATA = Path(__file__).resolve().parents[3] / "data" / "snapshot"


def _doc(**kw):
    return {
        "id": "x-v1", "url": "https://www.gov.pl/web/rcb/a1", "source": "rcb",
        "institution": "RCB", "doc_type": "komunikat", "region": None,
        "title": "Tytuł", "content": "Tytuł\nPierwszy akapit.\nDrugi akapit.",
        "published_at": "2026-11-20T10:00:00+01:00", "fetched_at": "2026-11-21T09:00:00+00:00", **kw,
    }


def _run(status, finished, message=None):
    return {"source": "rcb", "started_at": finished, "finished_at": finished, "status": status,
            "found": 0, "new": 0, "updated": 0, "unchanged": 0, "skipped": 0, "errors": 0, "message": message}


def _write(path: Path, rows, tail: str = "") -> Path:
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows) + tail, encoding="utf-8")
    return path


def test_article_drops_repeated_title(tmp_path):
    corpus = convert(_write(tmp_path / "d.jsonl", [_doc()]))
    (ev,) = corpus["evidence"]
    assert ev["kind"] == "article"
    assert ev["sections"][0]["text"] == "Pierwszy akapit.\n\nDrugi akapit."
    assert ev["published_at"] == "2026-11-20T09:00:00Z"


def test_missing_title_does_not_crash(tmp_path):
    corpus = convert(_write(tmp_path / "d.jsonl", [_doc(title=None)]))
    (ev,) = corpus["evidence"]
    assert ev["title"] == ev["citation_url"]
    assert ev["sections"][0]["text"].startswith("Tytuł")


def test_truncated_last_line_is_skipped(tmp_path):
    docs = _write(tmp_path / "d.jsonl", [_doc()], tail='{"id": "half-written')
    assert len(convert(docs)["evidence"]) == 1


def test_not_modified_counts_as_success(tmp_path):
    docs = _write(tmp_path / "d.jsonl", [_doc()])
    log = _write(tmp_path / "l.jsonl", [_run("ok", "2026-11-21T08:00:00+00:00"),
                                         _run("not_modified", "2026-11-21T09:00:00+00:00")])
    (src,) = convert(docs, log)["sources"]
    assert src["last_error"] is None
    assert src["last_success_at"] == "2026-11-21T09:00:00Z"


def test_failure_keeps_previous_success(tmp_path):
    docs = _write(tmp_path / "d.jsonl", [_doc()])
    log = _write(tmp_path / "l.jsonl", [_run("ok", "2026-11-21T08:00:00+00:00"),
                                         _run("throttled", "2026-11-21T09:00:00+00:00", "HTTP 429")])
    (src,) = convert(docs, log)["sources"]
    assert src["last_success_at"] == "2026-11-21T08:00:00Z"
    assert src["last_error"] == "throttled: HTTP 429"


@pytest.mark.skipif(not (DATA / "documents.jsonl").exists(), reason="no crawler snapshot in data/snapshot/")
def test_committed_snapshot_converts():
    corpus = convert(DATA / "documents.jsonl", DATA / "crawl_log.jsonl")
    assert len(corpus["evidence"]) == 98
    assert {e["kind"] for e in corpus["evidence"]} >= {"article", "warning_hydro", "warning_meteo"}


# --- API sources (RSO, IMGW SYNOP, GIOŚ): content as written by the crawler's json_api adapter --------------

def test_rso_alert_keeps_multiline_text_and_validity(tmp_path):
    content = ("id: 7\ntitle: Sztorm/2\ncontent: Nazwa biura: Gdynia\nPrzebieg: wiatr do 8 B\nrso_alarm: 0\n"
               "valid_from: 2026-11-21 18:00:00\nvalid_to: 2026-11-22 07:00:00\ncreated_at: 2026-11-21 17:57:29\n"
               "provinces.11.name: Pomorskie")
    doc = _doc(url="https://www.gov.pl/api/x/1#7", source="rso-meteorologiczne", doc_type="ostrzezenie_rso",
               title="Sztorm/2", content=content)
    (ev,) = convert(_write(tmp_path / "d.jsonl", [doc]))["evidence"]
    assert ev["kind"] == "alert"
    assert ev["sections"][0]["text"] == "Nazwa biura: Gdynia\n\nPrzebieg: wiatr do 8 B"
    assert (ev["valid_from"], ev["valid_to"]) == ("2026-11-21T17:00:00Z", "2026-11-22T06:00:00Z")
    assert ev["published_at"] == "2026-11-21T16:57:29Z"
    assert ev["areas"] == ["województwo pomorskie"]


def test_synop_becomes_structured_weather_reading(tmp_path):
    content = ("id_stacji: 12295\nstacja: Białystok\ndata_pomiaru: 2026-11-21\ngodzina_pomiaru: 20\n"
               "temperatura: 1.1\npredkosc_wiatru: 3\nwilgotnosc_wzgledna: 83.9\n\nŹródłem pochodzenia danych jest IMGW-PIB.")
    doc = _doc(url="https://danepubliczne.imgw.pl/api/data/synop#12295-2026-11-21-20", source="imgw-synop",
               doc_type="pomiar_synop", content=content)
    corpus = convert(_write(tmp_path / "d.jsonl", [doc]))
    (ev,) = corpus["evidence"]
    assert ev["kind"] == "weather_reading"
    assert ev["observed_at"] == ev["published_at"] == "2026-11-21T20:00:00Z"
    assert ev["fields"]["temperature"] == 1.1 and ev["fields"]["pressure"] is None
    assert corpus["sources"][0]["source_type"] == "structured"
    (p,) = chunk_record(ev)
    assert p.excerpt is None  # structured: rendered by a template, never quoted
    assert "Stacja: Białystok. Czas pomiaru: 21.11.2026 21:00" in p.text  # UTC 20:00 -> Warsaw 21:00
    assert "temperatura powietrza: 1,1°C; prędkość wiatru: 3 m/s; wilgotność względna: 83,9%." in p.text


def test_gios_becomes_structured_air_quality(tmp_path):
    content = ("Identyfikator stacji: 114\nNazwa stacji: Wrocław, ul. Bartnicza\nNazwa miasta: Wrocław\n"
               "Województwo: DOLNOŚLĄSKIE\nindeks.Data wykonania obliczeń indeksu: 2026-11-21 21:20:20\n"
               "indeks.Nazwa kategorii indeksu: Bardzo dobry\n"
               "indeks.Nazwa kategorii indeksu dla wskażnika PM10: Dobry\n\nŹródło danych: GIOŚ.")
    doc = _doc(url="https://api.gios.gov.pl/pjp-api/v1/rest/aqindex/getIndex/114#114-2026-11-21-21",
               source="gios-indeks-powietrza", doc_type="pomiar_powietrza", content=content)
    (ev,) = convert(_write(tmp_path / "d.jsonl", [doc]))["evidence"]
    assert ev["kind"] == "air_quality" and ev["observed_at"] == "2026-11-21T20:20:20Z"
    assert ev["areas"] == ["Wrocław", "województwo dolnośląskie"]
    assert ev["fields"]["index"] == "bardzo dobry" and ev["fields"]["pollutants"] == {"PM10": "dobry"}
    (p,) = chunk_record(ev)
    assert "Indeks ogólny: bardzo dobry. Poszczególne zanieczyszczenia: PM10: dobry." in p.text


def test_factcheck_sources_get_factcheck_type(tmp_path):
    case = _doc(url="https://euvsdisinfo.eu/report/x/", source="euvsdisinfo-cases", doc_type="weryfikacja",
                institution="EUvsDisinfo", title="X", content="Claim: X.\nFact Check: Not X.")
    analysis = _doc(url="https://euvsdisinfo.eu/y/", source="euvsdisinfo-articles", doc_type="analiza")
    corpus = convert(_write(tmp_path / "d.jsonl", [case, analysis, _doc()]))
    types = {s["source_id"]: s["source_type"] for s in corpus["sources"]}
    assert types == {"euvsdisinfo-cases": "factcheck", "euvsdisinfo-articles": "official", "rcb": "official"}
    assert corpus["evidence"][0]["kind"] == "article"  # record unchanged: no new version, no re-embedding


def test_catalog_supplies_licence_and_credit(tmp_path):
    docs = _write(tmp_path / "d.jsonl", [_doc()])
    cat = {"rcb": {"license": "CC BY-SA 4.0", "terms_url": "https://www.gov.pl/web/gov/prawa-autorskie",
                   "attribution": None, "processed_notice": None}}
    (src,) = convert(docs, catalog=cat)["sources"]
    assert src["license"] == "CC BY-SA 4.0" and src["terms_url"].endswith("prawa-autorskie")
    (bare,) = convert(docs)["sources"]
    assert bare["license"] is None and bare["terms_url"] is None


def test_every_crawler_source_has_a_licence():
    """The real sources.yaml: no source may be archived without a licence or legal basis."""
    from rag.crawler_import import source_catalog

    cat = source_catalog()
    assert cat and all(v["license"] for v in cat.values())
    assert cat["imgw-hydro"]["processed_notice"] and cat["imgw-hydro"]["attribution"]
