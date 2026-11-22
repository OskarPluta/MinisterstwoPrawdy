"""Request limits for the channels (API, Discord): per client and a global daily cap.

Each check costs LLM calls, so a public endpoint needs a brake against abuse and runaway cost.
Limits are sliding windows kept in memory, per process (the API and the Discord bot count
separately). Configuration (0 turns a limit off):

  PRAWDOWID_RATE_PER_MINUTE   checks per client per minute   (default 5)
  PRAWDOWID_RATE_PER_HOUR     checks per client per hour     (default 30)
  PRAWDOWID_DAILY_CHECKS      checks per process per 24 h    (default 1000, all clients together)
"""

from __future__ import annotations

import logging
import math
import os
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

log = logging.getLogger(__name__)

MINUTE, HOUR, DAY = 60.0, 3600.0, 86400.0
MAX_TRACKED_CLIENTS = 10_000  # idle clients are forgotten beyond this


@dataclass(frozen=True)
class Limited:
    """A refused request: how long to wait and what to tell the person (Polish, plain text)."""

    retry_after: int
    message: str
    scope: str  # "client" | "global"


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(0, int(raw))
    except ValueError:
        log.warning("%s=%r is not a number; using %d", name, raw, default)
        return default


def _wait_text(seconds: int) -> str:
    if seconds < 90:
        return "za minutę"
    minutes = math.ceil(seconds / 60)
    if minutes < 90:
        return f"za {minutes} min"
    return f"za {math.ceil(minutes / 60)} godz."


class RateLimiter:
    """Sliding-window limits per client key plus a global cap over 24 hours. Thread-safe."""

    def __init__(
        self, per_minute: int = 5, per_hour: int = 30, per_day_total: int = 1000,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.windows = [(w, n) for w, n in ((MINUTE, per_minute), (HOUR, per_hour)) if n > 0]
        self.per_day_total = per_day_total
        self.clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._all: deque[float] = deque()
        self._lock = threading.Lock()

    @classmethod
    def from_env(cls) -> RateLimiter:
        return cls(
            per_minute=_env_int("PRAWDOWID_RATE_PER_MINUTE", 5),
            per_hour=_env_int("PRAWDOWID_RATE_PER_HOUR", 30),
            per_day_total=_env_int("PRAWDOWID_DAILY_CHECKS", 1000),
        )

    def hit(self, key: str) -> Limited | None:
        """Count one request of `key`; returns None when allowed, Limited when refused
        (a refused request is not counted, so waiting is enough to get through)."""
        now = self.clock()
        horizon = max((w for w, _ in self.windows), default=0.0)
        with self._lock:
            while self._all and now - self._all[0] >= DAY:
                self._all.popleft()
            if self.per_day_total and len(self._all) >= self.per_day_total:
                wait = int(DAY - (now - self._all[0])) + 1
                return Limited(wait, "Dzienny limit sprawdzeń usługi został wyczerpany. "
                                     f"Spróbuj ponownie {_wait_text(wait)}.", "global")
            hits = self._hits.setdefault(key, deque())
            while hits and now - hits[0] >= horizon:
                hits.popleft()
            for window, limit in self.windows:
                recent = [t for t in hits if now - t < window]
                if len(recent) >= limit:
                    wait = int(window - (now - recent[0])) + 1
                    return Limited(wait, "Wysłano zbyt wiele próśb o sprawdzenie w krótkim czasie. "
                                         f"Spróbuj ponownie {_wait_text(wait)}.", "client")
            hits.append(now)
            self._all.append(now)
            if len(self._hits) > MAX_TRACKED_CLIENTS:
                self._forget_idle(now, horizon)
        return None

    def _forget_idle(self, now: float, horizon: float) -> None:
        for k in [k for k, h in self._hits.items() if not h or now - h[-1] >= horizon]:
            del self._hits[k]
