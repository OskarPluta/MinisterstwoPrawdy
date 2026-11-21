from datetime import timezone

from rag.timeutil import WARSAW, fmt_local, parse_ts


def test_parse_ts_is_aware_and_tolerant():
    def tz(value: str, **kw):
        dt = parse_ts(value, **kw)
        assert dt is not None
        return dt.tzinfo

    assert tz("2026-11-21T10:00:00Z") is not None
    assert tz("2026-11-21") == timezone.utc  # stored data: naive means UTC
    assert tz("2026-11-21", naive_tz=WARSAW) == WARSAW  # user-derived: Warsaw
    for bad in (None, "", "dziś", "21.11.2026"):
        assert parse_ts(bad) is None


def test_fmt_local_converts_to_warsaw():
    assert fmt_local("2026-11-21T10:00:00Z") == "21.11.2026 11:00"
    assert fmt_local("garbage") == "brak danych"
