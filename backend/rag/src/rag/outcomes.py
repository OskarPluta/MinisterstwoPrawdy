"""Outcome of a check, as one category for the admin panel and alerts.

The category is derived by code from the logged result (`CheckResult.to_dict()` or the
`result_json` of a `checks` row), never by the model, so old rows can be backfilled the
same way (rag/db.py, schema v5).

Groups:
    success    a verdict was given
    no_result  handled correctly, but the user got no verdict (no evidence, question back,
               out of scope, too long, unreadable material); not an error, no alert
    degraded   answered, but a part failed (one claim, a live source); warning alert
    failure    nothing useful came back (LLM outage, refusal, configuration, internal error);
               error alert (a refusal is a warning: it depends on the message, not on the system)
"""

from __future__ import annotations

import re
from typing import Any, Literal

Group = Literal["success", "no_result", "degraded", "failure"]

OUTCOMES: dict[str, tuple[str, Group]] = {
    "verdict": ("werdykt", "success"),
    "no_evidence": ("brak dowodów w bazie", "no_result"),
    "needs_clarification": ("pytanie o doprecyzowanie", "no_result"),
    "out_of_scope": ("poza zakresem", "no_result"),
    "too_long": ("za długa wiadomość", "no_result"),
    "unreadable": ("nieczytelny materiał", "no_result"),
    "partial_error": ("część twierdzeń nie sprawdzona", "degraded"),
    "live_error": ("błąd wyszukiwania na żywo", "degraded"),
    "llm_timeout": ("LLM: przekroczony czas", "failure"),
    "llm_rate_limit": ("LLM: limit zapytań", "failure"),
    "llm_auth": ("LLM: klucz lub uprawnienia", "failure"),
    "llm_refusal": ("LLM: odmowa", "failure"),
    "llm_error": ("LLM: błąd", "failure"),
    "config_error": ("błąd konfiguracji", "failure"),
    "internal_error": ("błąd wewnętrzny", "failure"),
}
OUTCOME_PL = {k: label for k, (label, _) in OUTCOMES.items()}
GROUP_OF = {k: group for k, (_, group) in OUTCOMES.items()}
FAILURE_OUTCOMES = frozenset(k for k, g in GROUP_OF.items() if g == "failure")
DEGRADED_OUTCOMES = frozenset(k for k, g in GROUP_OF.items() if g == "degraded")
NO_RESULT_OUTCOMES = frozenset(k for k, g in GROUP_OF.items() if g == "no_result")
ALERT_OUTCOMES = FAILURE_OUTCOMES | DEGRADED_OUTCOMES
WARNING_ONLY = frozenset({"llm_refusal"}) | DEGRADED_OUTCOMES

_STATUS_OUTCOMES = {"needs_clarification", "out_of_scope", "too_long", "unreadable"}
_VERDICTS = {"true", "false", "misleading"}
# psycopg errors: a database problem inside the pipeline is ours, not the LLM provider's.
_DB_ERRORS = {
    "OperationalError", "InterfaceError", "DatabaseError", "ProgrammingError", "IntegrityError",
    "DataError", "InternalError", "NotSupportedError", "UndefinedTable", "UndefinedColumn",
}
_REPR_TYPE = re.compile(r"^\s*([A-Za-z_][\w.]*)\(")


def severity(outcome: str) -> str:
    return "warning" if outcome in WARNING_ONLY else "error"


def _type_from(d: dict[str, Any]) -> str | None:
    """Exception class name: the stored `error_type`, else parsed from the `error` repr."""
    t = d.get("error_type")
    if t:
        return str(t)
    err = d.get("error")
    if isinstance(err, str) and (m := _REPR_TYPE.match(err)):
        return m.group(1).rsplit(".", 1)[-1]
    return None


def _failure(error_type: str | None) -> str:
    name = error_type or ""
    if name == "LLMRefusal":
        return "llm_refusal"
    if "Timeout" in name:
        return "llm_timeout"
    if "RateLimit" in name:
        return "llm_rate_limit"
    if name in ("AuthenticationError", "PermissionDeniedError"):
        return "llm_auth"
    if name in _DB_ERRORS:
        return "internal_error"
    return "llm_error"


def _live_error(live: Any) -> str | None:
    """Short error type of a failed live lookup (rag/live.py log), None when it worked or was skipped."""
    if not isinstance(live, dict):
        return None
    errors = [live.get("error")] + [s.get("error") for s in live.get("sources") or () if isinstance(s, dict)]
    for e in errors:
        if e:
            return str(e).split(":", 1)[0].split()[0]
    return None


def classify(payload: dict[str, Any]) -> tuple[str, str | None]:
    """(outcome, error_type) of a logged check. Priority: failure > status without a verdict >
    failed claim > failed live source > verdict / no evidence."""
    status = payload.get("status")
    claims = [c for c in payload.get("claims") or () if isinstance(c, dict)]
    if status == "error":
        et = _type_from(payload) or next((t for c in claims if (t := _type_from(c))), None)
        return _failure(et), et
    if status in _STATUS_OUTCOMES:
        return str(status), None
    failed = [c for c in claims if c.get("verdict") == "error"]
    if failed:
        return "partial_error", _type_from(failed[0])
    for c in claims:
        if (et := _live_error(c.get("live"))) is not None:
            return "live_error", et
    if any(c.get("verdict") in _VERDICTS for c in claims):
        return "verdict", None
    return "no_evidence", None


def usage_columns(payload: dict[str, Any], model: str | None) -> dict[str, Any]:
    """Token and cost columns of a `checks` row from the logged `usage` (cost in its own currency:
    PCSS models are priced in PLN)."""
    from .llm import Usage

    u = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    calls = int(u.get("calls") or 0)
    tin, tout = int(u.get("input_tokens") or 0), int(u.get("output_tokens") or 0)
    cost, currency = u.get("cost"), u.get("currency")
    if cost is None and u.get("cost_usd") is not None:
        cost, currency = u["cost_usd"], "USD"
    if cost is None and (tin or tout) and model:
        c = Usage(input_tokens=tin, output_tokens=tout).cost(model)
        if c is not None:
            cost, currency = round(c.amount, 6), c.currency
    return {
        "llm_calls": calls if u else None,
        "input_tokens": tin if u else None,
        "output_tokens": tout if u else None,
        "cost": float(cost) if cost is not None else None,
        "cost_currency": currency if cost is not None else None,
    }
