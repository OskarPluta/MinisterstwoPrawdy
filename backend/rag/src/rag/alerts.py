"""Alerts for the admin panel: processing problems written by any process (API, Discord bot,
CLI), shown and sent on by admin-backend (notifier).

One open alert per fingerprint: a repeat raises `count` instead of adding a row. Messages
are built by code from the outcome and the exception class name; they never contain the
user's text or the model's output.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from database import query

from .db import Connection
from .outcomes import ALERT_OUTCOMES, OUTCOME_PL, severity
from .timeutil import utcnow_iso

log = logging.getLogger(__name__)


def raise_alert(
    conn: Connection, *, kind: str, severity: str, fingerprint: str, message: str,
    check_id: str | None = None, once: bool = False,
) -> int | None:
    """Open (or repeat) an alert; returns its id. `once`: do nothing if this fingerprint was ever
    raised, also when acknowledged (state-based rules that would otherwise re-raise on every poll)."""
    if once and conn.execute(query("alerts.fingerprint_seen"), {"fingerprint": fingerprint}).fetchone():
        return None
    row = conn.execute(query("alerts.raise"), {
        "at": utcnow_iso(), "kind": kind, "severity": severity, "fingerprint": fingerprint,
        "check_id": check_id, "message": message,
    }).fetchone()
    return row["alert_id"] if row else None


def alert_for_check(conn: Connection, check_id: str, outcome: str, error_type: str | None) -> None:
    """Alert for a failed or degraded check. Never raises: an alert must not sink the check."""
    if outcome not in ALERT_OUTCOMES:
        return
    try:
        raise_alert(
            conn, kind="check", severity=severity(outcome),
            fingerprint=f"check:{outcome}:{error_type or '-'}",
            message=OUTCOME_PL[outcome] + (f" ({error_type})" if error_type else ""),
            check_id=check_id,
        )
    except Exception as e:  # noqa: BLE001 - logged; the check result is already stored
        log.error("cannot store alert for check %s: %r", check_id, e)


def list_alerts(conn: Connection, *, open_only: bool = False, limit: int = 200) -> list[dict[str, Any]]:
    return list(conn.execute(query("alerts.list"), {"open_only": open_only, "limit": limit}))


def open_count(conn: Connection) -> dict[str, int]:
    row = conn.execute(query("alerts.open_count")).fetchone() or {}
    return {"open": int(row.get("n") or 0), "errors": int(row.get("errors") or 0)}


def acknowledge(conn: Connection, alert_id: int) -> bool:
    return conn.execute(query("alerts.ack"), {"alert_id": alert_id, "at": utcnow_iso()}).fetchone() is not None


def acknowledge_all(conn: Connection) -> int:
    return conn.execute(query("alerts.ack_all"), {"at": utcnow_iso()}).rowcount


def alert_payload(row: dict[str, Any]) -> dict[str, Any]:
    """JSON-safe copy of an alerts row."""
    return json.loads(json.dumps(dict(row), default=str))
