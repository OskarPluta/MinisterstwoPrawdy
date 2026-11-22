"""Analyst export of the check log (rag/export.py): shape, filters, CSV safety, no internal fields."""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone

import pytest

from rag import export as ex
from rag.timeutil import WARSAW

CHECK_ID = "0b7f3c8e-1d2a-4c5b-9e6f-7a8b9c0d1e2f"


def _citation(n, role, url, publisher="IMGW-PIB"):
    return {"n": n, "passage_id": f"P{n}", "role": role, "title": "Ostrzeżenie", "publisher": publisher,
            "url": url, "date_label": "21.11.2026", "text": "Stan ostrzegawczy.", "is_verbatim": False,
            "attribution": "Źródłem pochodzenia danych jest IMGW-PIB."}


def _claim(text, verdict, citations=(), **kw):
    return {
        "claim": text, "verdict": verdict, "verdict_pl": kw.pop("verdict_pl", verdict), "explanation": "Wyjaśnienie.",
        "citations": list(citations), "notes": kw.pop("notes", []), "shortlist_ids": ["P1", "P2"],
        "invented_ids": ["P999"], "downgraded_from": None, "conflict": False, "known_disinformation": False,
        "strength": {"level": "strong", "level_pl": "silna", "reasons": ["Niezależne źródła: 2.", "Rodzaj: urzędowe."]},
        "error": "internal traceback", "live": {"queried": ["gov"]}, **kw,
    }


def _row(check_id, created_at, status="ok", claims=(), **kw):
    result = {"check_id": check_id, "status": status, "message": kw.pop("message", None), "claims": list(claims),
              "injection_detected": False, "duration_ms": 1234, "error": "internal"}
    return {"check_id": check_id, "created_at": created_at, "status": status,
            "result_json": json.dumps(result, ensure_ascii=False), "corpus_version": 7, "model": "gpt-test",
            "embedding_model": "hash", "duration_ms": 1234, "shared": kw.pop("shared", False)}


ROWS = [
    _row(CHECK_ID, "2026-11-21T10:00:00Z", claims=[
        _claim("Na Wiśle obowiązuje ostrzeżenie", "true",
               [_citation(1, "supports", "https://imgw.test/a"), _citation(2, "context", "https://imgw.test/b", "RCB")]),
        _claim("Szkoły są zamknięte", "false", [_citation(1, "contradicts", "https://gov.test/c", "MEN")]),
    ], shared=True),
    _row("11111111-2222-4333-8444-555555555555", "2026-11-22T23:30:00Z", status="out_of_scope",
         message="Nie znalazłem twierdzenia."),
    _row("22222222-3333-4444-8555-666666666666", "2026-10-01T08:00:00", claims=[_claim("Stare", "insufficient")]),
]


def _csv(text: str) -> list[dict[str, str]]:
    assert text.startswith("﻿")
    return list(csv.DictReader(io.StringIO(text[1:], newline="")))


def test_json_shape_versioned_and_without_internal_fields():
    f = ex.ExportFilters()
    doc = json.loads(ex.to_json(ex.select(ROWS, f), f, exported_at="2026-11-22T12:00:00Z"))
    assert doc["format"] == "prawdowid-export" and doc["version"] == 1
    assert doc["exported_at"] == "2026-11-22T12:00:00Z" and doc["count"] == 3 and doc["notice"]
    assert doc["filters"] == {"since": None, "until": None, "status": None, "verdict": None}
    first = next(c for c in doc["checks"] if c["checked_at"] == "2026-11-21T10:00:00Z")
    assert set(first) == {"check_ref", "checked_at", "checked_at_local", "status", "message", "injection_detected",
                          "shared", "corpus_version", "model", "embedding_model", "duration_ms", "claims"}
    claim = first["claims"][0]
    assert set(claim) == {"claim", "verdict", "verdict_pl", "downgraded_from", "explanation", "strength",
                          "known_disinformation", "conflict", "notes", "citations"}
    assert set(claim["citations"][0]) == {"n", "role", "title", "publisher", "url", "date_label", "text",
                                          "is_verbatim", "attribution"}
    assert first["shared"] is True and first["model"] == "gpt-test" and first["corpus_version"] == 7
    raw = json.dumps(doc)
    for internal in ("shortlist_ids", "invented_ids", "passage_id", "internal traceback", '"live"', '"error"',
                     "input_masked", "share_id"):
        assert internal not in raw


def test_check_id_never_leaves_only_a_stable_hash():
    out = ex.to_json(ex.select(ROWS, ex.ExportFilters())) + ex.to_csv(ex.select(ROWS, ex.ExportFilters()))
    for row in ROWS:
        assert row["check_id"] not in out
    ref = ex.check_ref(CHECK_ID)
    assert len(ref) == 16 and ref in out and ref == ex.check_ref(CHECK_ID), "stable across exports"
    assert ref != ex.check_ref("11111111-2222-4333-8444-555555555555")


def test_times_in_utc_and_warsaw_ordered_oldest_first():
    recs = ex.select(ROWS, ex.ExportFilters())
    assert [r["checked_at"] for r in recs] == ["2026-10-01T08:00:00Z", "2026-11-21T10:00:00Z", "2026-11-22T23:30:00Z"]
    assert recs[0]["checked_at_local"] == "2026-10-01T10:00:00+02:00", "naive stored time is UTC; CEST in October"
    assert recs[2]["checked_at_local"] == "2026-11-23T00:30:00+01:00", "past midnight in Warsaw"
    rows = _csv(ex.to_csv(recs))
    assert rows[-1]["checked_at_warsaw"] == "2026-11-23 00:30:00"
    assert rows[-1]["checked_at_utc"] == "2026-11-22T23:30:00Z"


def test_csv_one_row_per_claim_and_one_for_a_check_without_claims():
    rows = _csv(ex.to_csv(ex.select(ROWS, ex.ExportFilters())))
    assert list(rows[0]) == list(ex.CSV_COLUMNS)
    assert len(rows) == 4  # 1 (old) + 2 claims + 1 out_of_scope without claims
    true_row, false_row = rows[1], rows[2]
    assert (true_row["claim_no"], true_row["claims_in_check"], true_row["verdict"]) == ("1", "2", "true")
    assert true_row["citation_count"] == "2" and true_row["citations_supports"] == "1"
    assert true_row["citation_urls"] == "https://imgw.test/a | https://imgw.test/b"
    assert true_row["publishers"] == "IMGW-PIB | RCB"
    assert true_row["strength"] == "strong" and true_row["strength_reasons"] == "Niezależne źródła: 2. | Rodzaj: urzędowe."
    assert true_row["known_disinformation"] == "false" and true_row["shared"] == "true"
    assert false_row["citations_contradicts"] == "1" and false_row["check_ref"] == true_row["check_ref"]
    empty = rows[3]
    assert empty["status"] == "out_of_scope" and empty["claim"] == "" and empty["claims_in_check"] == "0"
    assert empty["message"] == "Nie znalazłem twierdzenia."


@pytest.mark.parametrize("evil", ["=HYPERLINK(\"http://x.test\")", "+1+1", "-2+3", "@SUM(A1)", "\tcmd", "\rx",
                                  "＝1+1"])
def test_csv_neutralises_formulas(evil):
    row = _row(CHECK_ID, "2026-11-21T10:00:00Z", claims=[_claim(evil, "false", [_citation(1, "contradicts", None, evil)],
                                                                 notes=[evil])], message=evil)
    rows = _csv(ex.to_csv(ex.select([row], ex.ExportFilters())))
    for col in ("claim", "publishers", "notes", "message"):
        assert rows[0][col] == "'" + evil, col
    assert ex.safe_cell("Spokojnie = bezpiecznie") == "Spokojnie = bezpiecznie", "only a leading sign matters"
    assert ex.safe_cell(-5) == -5 and ex.safe_cell(None) == "" and ex.safe_cell(True) == "true"


def test_csv_quoting_survives_commas_quotes_and_newlines():
    tricky = 'Mówi "tak", potem\nnie; 100%'
    row = _row(CHECK_ID, "2026-11-21T10:00:00Z", claims=[_claim(tricky, "misleading")])
    text = ex.to_csv(ex.select([row], ex.ExportFilters()))
    assert _csv(text)[0]["claim"] == tricky
    assert ex.to_csv([], bom=False) == ",".join(ex.CSV_COLUMNS) + "\r\n"


def test_user_text_is_masked_again():
    row = _row(CHECK_ID, "2026-11-21T10:00:00Z", claims=[_claim("Pisz na jan@example.com", "insufficient")],
               message="Tel. 600 700 800")
    rec = ex.select([row], ex.ExportFilters())[0]
    assert rec["claims"][0]["claim"] == "Pisz na [email]" and rec["message"] == "Tel. [telefon]"


def test_filters_time_status_verdict():
    def refs(**kw):
        return [r["checked_at"] for r in ex.select(ROWS, ex.ExportFilters.parse(**kw))]

    # since inclusive, until exclusive; no offset = Europe/Warsaw (2026-11-21 11:00 Warsaw = 10:00 UTC).
    assert refs(since="2026-11-21T11:00:00") == ["2026-11-21T10:00:00Z", "2026-11-22T23:30:00Z"]
    assert refs(until="2026-11-21T11:00:00") == ["2026-10-01T08:00:00Z"]
    assert refs(since="2026-11-23", until="2026-11-24") == ["2026-11-22T23:30:00Z"], "Warsaw day, not UTC day"
    assert refs(statuses=["out_of_scope,ok"]) == ["2026-10-01T08:00:00Z", "2026-11-21T10:00:00Z", "2026-11-22T23:30:00Z"]
    assert refs(statuses=["out_of_scope"]) == ["2026-11-22T23:30:00Z"]
    (rec,) = ex.select(ROWS, ex.ExportFilters.parse(verdicts=["false"]))
    assert [c["verdict"] for c in rec["claims"]] == ["false"], "only matching claims stay"
    assert ex.ExportFilters.parse(since="2026-11-21").as_dict()["since"] == "2026-11-20T23:00:00Z"


def test_unreadable_time_kept_only_without_time_filter():
    rows = [_row(CHECK_ID, "wczoraj", claims=[_claim("x", "true")])]
    assert ex.select(rows, ex.ExportFilters())[0]["checked_at"] is None
    assert ex.select(rows, ex.ExportFilters(since=datetime(2026, 1, 1, tzinfo=timezone.utc))) == []
    assert _csv(ex.to_csv(ex.select(rows, ex.ExportFilters())))[0]["checked_at_warsaw"] == ""


@pytest.mark.parametrize("kw", [{"since": "wczoraj"}, {"statuses": ["done"]}, {"verdicts": ["maybe"]}])
def test_bad_filters_rejected(kw):
    with pytest.raises(ValueError):
        ex.ExportFilters.parse(**kw)


def test_filename_uses_warsaw_date():
    assert ex.filename("csv", datetime(2026, 11, 22, 23, 30, tzinfo=timezone.utc)) == "prawdowid-export-2026-11-23.csv"
    assert ex.filename("json", datetime(2026, 11, 22, 12, 0, tzinfo=WARSAW)) == "prawdowid-export-2026-11-22.json"


def test_load_reads_the_check_log(new_db):
    """Named query checks.export against a real database: no input text, share reduced to a flag."""
    from rag.db import record_check, share_check

    c = new_db()
    for r in ROWS[:2]:
        record_check(c, {**{k: r[k] for k in ("check_id", "created_at", "status", "result_json", "corpus_version",
                                               "model", "embedding_model", "duration_ms")},
                         "input_masked": "SEKRETNA TREŚĆ UŻYTKOWNIKA"})
    share_check(c, CHECK_ID)
    recs = ex.load(c, ex.ExportFilters())
    assert [r["status"] for r in recs] == ["ok", "out_of_scope"]
    assert recs[0]["shared"] is True and recs[1]["shared"] is False
    out = ex.to_json(recs) + ex.to_csv(recs)
    assert "SEKRETNA" not in out and CHECK_ID not in out


def test_cli_export(new_db, monkeypatch, tmp_path):
    from typer.testing import CliRunner

    from rag import cli
    from rag.db import record_check

    c = new_db()
    r = ROWS[0]
    record_check(c, {**{k: r[k] for k in ("check_id", "created_at", "status", "result_json", "corpus_version",
                                           "model", "embedding_model", "duration_ms")}, "input_masked": "x"})
    monkeypatch.setattr(cli, "connect", lambda url: c)
    runner = CliRunner()

    res = runner.invoke(cli.app, ["export", "--format", "json", "--verdict", "false"])
    assert res.exit_code == 0, res.output
    doc = json.loads(res.stdout)
    assert doc["filters"]["verdict"] == ["false"] and [x["verdict"] for x in doc["checks"][0]["claims"]] == ["false"]

    out = tmp_path / "e.csv"
    res = runner.invoke(cli.app, ["export", "--format", "csv", "--since", "2026-11-21", "--out", str(out)])
    assert res.exit_code == 0, res.output
    data = out.read_bytes()
    assert data.startswith(b"\xef\xbb\xbfcheck_ref,") and data.count(b"\r\n") == 3  # header + 2 claims
    assert CHECK_ID.encode() not in data

    res = runner.invoke(cli.app, ["export", "--format", "csv", "--no-bom"])
    assert res.stdout.startswith("check_ref,")
    assert runner.invoke(cli.app, ["export", "--format", "xml"]).exit_code != 0
    assert runner.invoke(cli.app, ["export", "--since", "wczoraj"]).exit_code != 0
