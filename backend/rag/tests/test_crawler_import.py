"""crawler_import.convert: crawler output -> corpus JSON (no database needed)."""

import json
from pathlib import Path

import pytest

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
