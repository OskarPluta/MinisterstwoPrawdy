"""Alert rules and the Discord webhook, run by the admin process in a background thread.

Every poll it:
  1. evaluates rules: the share of failed checks in a recent window, and sources whose last
     fetch failed (or, with ADMIN_ALERT_STALE_HOURS, that have been silent too long);
  2. sends open alerts not yet sent (or that repeated since, at most every RENOTIFY_S) to
     ADMIN_ALERT_DISCORD_WEBHOOK. Without a webhook alerts are only shown in the panel.

Alerts raised by other processes while this one is down are sent when it starts. Webhook
messages are built from alert rows only (outcome, exception class, counts, source names):
never the user's text or the model's output.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from database import query
from rag import admin_db
from rag.alerts import raise_alert
from rag.db import Connection
from rag.outcomes import FAILURE_OUTCOMES
from rag.timeutil import parse_ts

from .reports import iso

log = logging.getLogger(__name__)

DISCORD_LIMIT = 2000
MAX_LINES = 10
RENOTIFY_S = 30 * 60
_MD = re.compile(r"([\\*_~`|>@#\[\]()])")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name) or default)
    except ValueError:
        return default


@dataclass
class NotifierConfig:
    webhook_url: str | None = None
    poll_s: float = 30.0
    error_rate: float = 0.2      # share of failed checks in the window that raises an alert
    min_checks: int = 5          # ... over at least this many checks
    window_min: int = 15
    stale_hours: float = 0.0     # 0: off (the frozen demo snapshot would always be "stale")
    public_url: str | None = None  # admin panel address, for links in messages

    @classmethod
    def from_env(cls) -> NotifierConfig:
        return cls(
            webhook_url=(os.environ.get("ADMIN_ALERT_DISCORD_WEBHOOK") or "").strip() or None,
            poll_s=max(5.0, _env_float("ADMIN_ALERT_POLL_S", 30.0)),
            error_rate=_env_float("ADMIN_ALERT_ERROR_RATE", 0.2),
            min_checks=int(_env_float("ADMIN_ALERT_MIN_CHECKS", 5)),
            window_min=int(_env_float("ADMIN_ALERT_WINDOW_MIN", 15)),
            stale_hours=_env_float("ADMIN_ALERT_STALE_HOURS", 0.0),
            public_url=(os.environ.get("ADMIN_PUBLIC_URL") or "").rstrip("/") or None,
        )


def _md(text: str) -> str:
    """Text as literal Discord markdown (no formatting, no mentions)."""
    return _MD.sub(r"\\\1", text)


def _short(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:10]


@dataclass
class Notifier:
    config: NotifierConfig
    transport: httpx.BaseTransport | None = None
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(timezone.utc))
    _backoff_until: float = 0.0
    _stop: threading.Event = field(default_factory=threading.Event)
    _thread: threading.Thread | None = None

    # ---- rules ---------------------------------------------------------------------------
    def evaluate_rules(self, conn: Connection) -> None:
        now = self.clock()
        cfg = self.config
        rows = admin_db.checks_in_range(conn, iso(now - timedelta(minutes=cfg.window_min)))
        rows = [r for r in rows if r["channel"] != "eval"]
        failed = sum(1 for r in rows if r["outcome"] in FAILURE_OUTCOMES)
        if len(rows) >= cfg.min_checks and failed / len(rows) >= cfg.error_rate:
            raise_alert(
                conn, kind="rule", severity="error", fingerprint="rule:error_rate",
                message=f"{failed} z {len(rows)} sprawdzeń zakończyło się błędem w ostatnich {cfg.window_min} min",
            )
        for s in conn.execute(query("sources.status_rows")).fetchall():
            sid = s["source_id"]
            if not s["enabled"] or sid.startswith(("live-", "admin-")):
                continue  # live and hand-added sources are not refreshed on a schedule
            if s["last_error"]:
                raise_alert(
                    conn, kind="source", severity="warning",
                    fingerprint=f"source:{sid}:{_short(s['last_error'])}",
                    message=f"Źródło {s['publisher']} ({sid}): ostatnie pobranie nie powiodło się", once=True,
                )
            last = parse_ts(s["last_success_at"])
            if cfg.stale_hours and (last is None or last < now - timedelta(hours=cfg.stale_hours)):
                raise_alert(
                    conn, kind="source", severity="warning",
                    fingerprint=f"source-stale:{sid}:{s['last_success_at'] or 'never'}",
                    message=f"Źródło {s['publisher']} ({sid}): brak udanego pobrania od ponad "
                            f"{cfg.stale_hours:g} h", once=True,
                )

    # ---- delivery ------------------------------------------------------------------------
    def due(self, conn: Connection) -> list[dict[str, Any]]:
        now = self.clock()
        out = []
        for a in conn.execute(query("alerts.open_for_notify")).fetchall():
            sent = parse_ts(a["notified_at"])
            if sent is None or (a["count"] > a["notified_count"] and sent <= now - timedelta(seconds=RENOTIFY_S)):
                out.append(dict(a))
        return out

    def format(self, alerts: list[dict[str, Any]]) -> str:
        lines = ["**Prawdowid: alerty panelu administracyjnego**"]
        for a in alerts[:MAX_LINES]:
            icon = "🔴" if a["severity"] == "error" else "🟡"
            repeat = f" ×{a['count']}" if a["count"] > 1 else ""
            line = f"{icon} {_md(a['message'])}{repeat}"
            if self.config.public_url and a.get("check_id"):
                line += f" <{self.config.public_url}/historia?id={a['check_id']}>"
            lines.append(line)
        if len(alerts) > MAX_LINES:
            lines.append(f"… i {len(alerts) - MAX_LINES} innych")
        if self.config.public_url:
            lines.append(f"<{self.config.public_url}/alerty>")
        text = "\n".join(lines)
        return text if len(text) <= DISCORD_LIMIT else text[: DISCORD_LIMIT - 1] + "…"

    def send_pending(self, conn: Connection) -> int:
        """Number of alerts delivered (0 without a webhook, while backing off, or on failure)."""
        if not self.config.webhook_url or time.monotonic() < self._backoff_until:
            return 0
        alerts = self.due(conn)
        if not alerts:
            return 0
        payload = {"content": self.format(alerts), "allowed_mentions": {"parse": []}}
        try:
            with httpx.Client(timeout=10.0, transport=self.transport, trust_env=False) as client:
                r = client.post(self.config.webhook_url, json=payload)
        except httpx.HTTPError as e:
            log.warning("alert webhook failed: %s", type(e).__name__)
            self._backoff_until = time.monotonic() + 60
            return 0
        if r.status_code == 429:
            try:
                retry = float(r.json().get("retry_after", 5))
            except (ValueError, AttributeError):
                retry = 5.0
            self._backoff_until = time.monotonic() + max(1.0, retry)
            return 0
        if r.status_code >= 400:
            log.warning("alert webhook answered HTTP %s", r.status_code)
            self._backoff_until = time.monotonic() + 60
            return 0
        at = iso(self.clock())
        for a in alerts[:MAX_LINES]:  # the rest goes in the next message
            conn.execute(query("alerts.mark_notified"), {"alert_id": a["alert_id"], "at": at, "count": a["count"]})
        return min(len(alerts), MAX_LINES)

    def run_once(self, conn: Connection) -> int:
        self.evaluate_rules(conn)
        return self.send_pending(conn)

    # ---- thread --------------------------------------------------------------------------
    def start(self, conn_factory: Callable[[], Connection]) -> None:
        def loop() -> None:
            conn: Connection | None = None
            while not self._stop.is_set():
                try:
                    if conn is None or conn.closed:
                        conn = conn_factory()
                    self.run_once(conn)
                except Exception as e:  # noqa: BLE001 - keep polling; the database may come back
                    log.error("alert notifier: %r", e)
                    if conn is not None:
                        conn.close()
                    conn = None
                self._stop.wait(self.config.poll_s)
            if conn is not None:
                conn.close()

        self._thread = threading.Thread(target=loop, name="admin-notifier", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
