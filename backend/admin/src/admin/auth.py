"""One shared admin password, a signed session cookie, a CSRF header and a login throttle.

    ADMIN_PASSWORD   required, at least 12 characters
    ADMIN_SECRET     required, at least 32 characters; signs the session cookie. Changing it
                     logs every session out (sessions are not stored).
    ADMIN_COOKIE_SECURE=1   mark the cookie Secure (behind HTTPS)

The service refuses to start without both values, like the crawler without a contact mail.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field

from fastapi import HTTPException, Request

COOKIE = "mp_admin"
SESSION_TTL_S = 12 * 3600
# Every request that changes something must carry this header. A cross-site form cannot set it,
# and SameSite=Strict already keeps the cookie off cross-site requests (defence in depth).
CSRF_HEADER = "x-requested-with"
CSRF_VALUE = "admin-panel"
MIN_PASSWORD = 12
MIN_SECRET = 32
LOGIN_MAX_FAILURES = 5
LOGIN_WINDOW_S = 300.0

MSG_LOGIN = "Nieprawidłowe hasło."
MSG_THROTTLED = "Za dużo nieudanych prób logowania. Spróbuj ponownie za kilka minut."
MSG_SESSION = "Sesja wygasła albo nie jesteś zalogowany."
MSG_CSRF = "Brak nagłówka panelu administracyjnego."


@dataclass(frozen=True)
class AuthConfig:
    password: str
    secret: bytes
    cookie_secure: bool = False
    ttl_s: int = SESSION_TTL_S

    @classmethod
    def from_env(cls) -> AuthConfig:
        password = os.environ.get("ADMIN_PASSWORD") or ""
        secret = os.environ.get("ADMIN_SECRET") or ""
        if len(password) < MIN_PASSWORD:
            raise SystemExit(f"ADMIN_PASSWORD is not set or shorter than {MIN_PASSWORD} characters (see .env.example)")
        if len(secret) < MIN_SECRET:
            raise SystemExit(
                f"ADMIN_SECRET is not set or shorter than {MIN_SECRET} characters; generate one with "
                "`python -c \"import secrets; print(secrets.token_urlsafe(48))\"`"
            )
        return cls(password, secret.encode(), cookie_secure=os.environ.get("ADMIN_COOKIE_SECURE") == "1")

    def password_ok(self, given: str) -> bool:
        return hmac.compare_digest(given.encode(), self.password.encode())

    def _sign(self, payload: str) -> str:
        return hmac.new(self.secret, payload.encode(), hashlib.sha256).hexdigest()

    def make_token(self, now: float | None = None) -> str:
        exp = int((now if now is not None else time.time()) + self.ttl_s)
        payload = f"{exp}.{secrets.token_hex(8)}"
        return f"{payload}.{self._sign(payload)}"

    def token_expiry(self, token: str | None, now: float | None = None) -> int | None:
        """Expiry (unix time) of a valid, unexpired token, else None."""
        if not token or token.count(".") != 2:
            return None
        exp, nonce, sig = token.split(".")
        if not hmac.compare_digest(sig, self._sign(f"{exp}.{nonce}")):
            return None
        try:
            expiry = int(exp)
        except ValueError:
            return None
        return expiry if expiry > (now if now is not None else time.time()) else None


@dataclass
class LoginThrottle:
    """Failed logins per client address in a sliding window (in memory, per process)."""

    max_failures: int = LOGIN_MAX_FAILURES
    window_s: float = LOGIN_WINDOW_S
    _fails: dict[str, deque[float]] = field(default_factory=lambda: defaultdict(deque))
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def _trim(self, key: str, now: float) -> deque[float]:
        q = self._fails[key]
        while q and q[0] <= now - self.window_s:
            q.popleft()
        return q

    def blocked(self, key: str) -> bool:
        with self._lock:
            return len(self._trim(key, time.monotonic())) >= self.max_failures

    def failed(self, key: str) -> None:
        with self._lock:
            now = time.monotonic()
            self._trim(key, now).append(now)

    def reset(self, key: str) -> None:
        with self._lock:
            self._fails.pop(key, None)


def client_key(request: Request) -> str:
    return request.client.host if request.client else "?"


def require_session(request: Request, cfg: AuthConfig) -> int:
    """The session's expiry; 401 without a valid session, 403 on a change without the CSRF header."""
    expiry = cfg.token_expiry(request.cookies.get(COOKIE))
    if expiry is None:
        raise HTTPException(401, MSG_SESSION)
    if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get(CSRF_HEADER) != CSRF_VALUE:
        raise HTTPException(403, MSG_CSRF)
    return expiry
