"""Aggregates for the admin panel, computed in Python over `checks` rows.

Days are Europe/Warsaw calendar days (times in the database are UTC). Costs are summed per
currency, never converted: PCSS models are priced in PLN, OpenAI and Anthropic in USD.
"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from rag import admin_db
from rag.db import Connection
from rag.llm import Usage
from rag.outcomes import FAILURE_OUTCOMES, GROUP_OF, OUTCOME_PL, OUTCOMES
from rag.timeutil import WARSAW, parse_ts

GROUPS = ("success", "no_result", "degraded", "failure")
DEFAULT_RANGE_DAYS = 30
MAX_RANGE_DAYS = 400


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def warsaw_day(created_at: str) -> str:
    dt = parse_ts(created_at)
    return dt.astimezone(WARSAW).date().isoformat() if dt else "?"


def day_range(start: str | None, end: str | None, today: date | None = None) -> tuple[date, date]:
    """Inclusive Warsaw-day range from YYYY-MM-DD strings (default: the last 30 days)."""
    today = today or datetime.now(WARSAW).date()
    try:
        d_to = date.fromisoformat(end) if end else today
        d_from = date.fromisoformat(start) if start else d_to - timedelta(days=DEFAULT_RANGE_DAYS - 1)
    except ValueError:
        raise ValueError("daty w formacie RRRR-MM-DD") from None
    if d_from > d_to:
        raise ValueError("data początkowa jest po końcowej")
    if (d_to - d_from).days > MAX_RANGE_DAYS:
        raise ValueError(f"zakres najwyżej {MAX_RANGE_DAYS} dni")
    return d_from, d_to


def utc_bounds(d_from: date, d_to: date) -> tuple[str, str]:
    """[since, until) in UTC ISO for Warsaw midnights of d_from and the day after d_to."""
    since = datetime.combine(d_from, time.min, WARSAW)
    until = datetime.combine(d_to + timedelta(days=1), time.min, WARSAW)
    return iso(since), iso(until)


def _days(d_from: date, d_to: date) -> list[str]:
    return [(d_from + timedelta(days=i)).isoformat() for i in range((d_to - d_from).days + 1)]


def _cost(rows: list[dict[str, Any]]) -> dict[str, float]:
    out: Counter[str] = Counter()
    for r in rows:
        if r.get("cost") is not None and r.get("cost_currency"):
            out[r["cost_currency"]] += float(r["cost"])
    return {k: round(v, 6) for k, v in sorted(out.items())}


def _window(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_outcome = Counter(r["outcome"] or "?" for r in rows)
    by_group = Counter(GROUP_OF.get(r["outcome"] or "", "failure") for r in rows)
    durations = sorted(int(r["duration_ms"]) for r in rows if r["duration_ms"] is not None)
    total = len(rows)
    return {
        "total": total,
        "by_group": {g: by_group.get(g, 0) for g in GROUPS},
        "by_outcome": dict(by_outcome.most_common()),
        "failure_rate": round(by_group.get("failure", 0) / total, 4) if total else 0.0,
        "cost": _cost(rows),
        "calls": sum(int(r["llm_calls"] or 0) for r in rows),
        "tokens": {"input": sum(int(r["input_tokens"] or 0) for r in rows),
                   "output": sum(int(r["output_tokens"] or 0) for r in rows)},
        "duration_ms": {
            "median": int(statistics.median(durations)) if durations else 0,
            "p95": durations[min(len(durations) - 1, int(len(durations) * 0.95))] if durations else 0,
        },
    }


def overview(conn: Connection, now: datetime | None = None, days: int = 14) -> dict[str, Any]:
    from rag.alerts import open_count
    from rag.db import corpus_totals

    now = now or datetime.now(timezone.utc)
    today = now.astimezone(WARSAW).date()
    d_from = today - timedelta(days=days - 1)
    since, _ = utc_bounds(d_from, today)
    rows = admin_db.checks_in_range(conn, since)
    cut_24h, cut_7d = iso(now - timedelta(hours=24)), iso(now - timedelta(days=7))
    per_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        per_day[warsaw_day(r["created_at"])].append(r)
    sources = admin_db.admin_sources(conn)
    return {
        "generated_at": iso(now),
        "windows": {
            "24h": _window([r for r in rows if r["created_at"] >= cut_24h]),
            "7d": _window([r for r in rows if r["created_at"] >= cut_7d]),
        },
        "daily": [
            {"day": d, **{g: n for g, n in _window(per_day.get(d, []))["by_group"].items()},
             "total": len(per_day.get(d, [])), "cost": _cost(per_day.get(d, []))}
            for d in _days(d_from, today)
        ],
        "alerts": open_count(conn),
        "sources": {
            "total": len(sources),
            "disabled": sum(1 for s in sources if not s["enabled"]),
            "with_errors": sum(1 for s in sources if s["enabled"] and s["last_error"]),
        },
        "corpus": corpus_totals(conn),
    }


def failures(conn: Connection, d_from: date, d_to: date) -> dict[str, Any]:
    """Checks without a verdict, by category: technical failures, degraded answers, and
    correctly handled messages that still gave the user no verdict."""
    since, until = utc_bounds(d_from, d_to)
    rows = [r for r in admin_db.checks_in_range(conn, since, until) if GROUP_OF.get(r["outcome"] or "") != "success"]
    by_outcome = Counter(r["outcome"] or "?" for r in rows)
    per_day: dict[str, Counter[str]] = defaultdict(Counter)
    for r in rows:
        per_day[warsaw_day(r["created_at"])][GROUP_OF.get(r["outcome"] or "", "failure")] += 1
    types = Counter((r["error_type"], r["outcome"]) for r in rows if r["error_type"])
    return {
        "from": d_from.isoformat(), "to": d_to.isoformat(),
        "by_outcome": [{"outcome": o, "label": OUTCOME_PL.get(o, o), "group": GROUP_OF.get(o, "failure"), "count": n}
                       for o, n in by_outcome.most_common()],
        "daily": [{"day": d, **{g: per_day[d].get(g, 0) for g in GROUPS if g != "success"}}
                  for d in _days(d_from, d_to)],
        "error_types": [{"error_type": t, "outcome": o, "count": n} for (t, o), n in types.most_common(20)],
        "unreviewed": sum(1 for r in rows if r["outcome"] in FAILURE_OUTCOMES and not r["reviewed"]),
    }


USAGE_GROUPS = ("day", "model", "channel", "call")


def usage(conn: Connection, d_from: date, d_to: date, group: str = "day") -> dict[str, Any]:
    if group not in USAGE_GROUPS:
        raise ValueError(f"group: one of {', '.join(USAGE_GROUPS)}")
    since, until = utc_bounds(d_from, d_to)
    rows = admin_db.checks_in_range(conn, since, until)
    out: list[dict[str, Any]] = []
    if group == "call":
        acc: dict[str, dict[str, Any]] = {}
        for c in admin_db.usage_calls_in_range(conn, since, until):
            a = acc.setdefault(c["call"], {"key": c["call"], "checks": None, "calls": 0, "input_tokens": 0,
                                           "output_tokens": 0, "cost": Counter()})
            a["calls"] += 1
            a["input_tokens"] += int(c.get("in") or 0)
            a["output_tokens"] += int(c.get("out") or 0)
            cost = Usage(input_tokens=int(c.get("in") or 0), output_tokens=int(c.get("out") or 0)).cost(c["model"] or "")
            if cost is not None:
                a["cost"][cost.currency] += cost.amount
        out = [{**a, "cost": {k: round(v, 6) for k, v in sorted(a["cost"].items())}} for a in acc.values()]
        out.sort(key=lambda a: -a["input_tokens"])
    else:
        buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in rows:
            key = warsaw_day(r["created_at"]) if group == "day" else (r[group] or "—")
            buckets[key].append(r)
        keys = _days(d_from, d_to) if group == "day" else sorted(buckets, key=lambda k: -len(buckets[k]))
        for k in keys:
            b = buckets.get(k, [])
            out.append({
                "key": k, "checks": len(b), "calls": sum(int(r["llm_calls"] or 0) for r in b),
                "input_tokens": sum(int(r["input_tokens"] or 0) for r in b),
                "output_tokens": sum(int(r["output_tokens"] or 0) for r in b), "cost": _cost(b),
            })
    return {
        "from": d_from.isoformat(), "to": d_to.isoformat(), "group": group, "rows": out,
        "totals": {
            "checks": len(rows), "calls": sum(int(r["llm_calls"] or 0) for r in rows),
            "input_tokens": sum(int(r["input_tokens"] or 0) for r in rows),
            "output_tokens": sum(int(r["output_tokens"] or 0) for r in rows), "cost": _cost(rows),
            "unpriced_checks": sum(1 for r in rows if (r["llm_calls"] or 0) and r["cost"] is None),
        },
    }


def outcome_catalog() -> list[dict[str, str]]:
    return [{"key": k, "label": label, "group": group} for k, (label, group) in OUTCOMES.items()]
