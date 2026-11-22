"""HTTP API for the web frontend. Routes live under /api (the frontend proxies /api/*).

    GET  /api/health    database reachable, corpus version
    POST /api/verify    {text, now?} -> public view of rag.verify.CheckResult
    GET  /api/sources   source catalog for audit + corpus totals

Verification logic stays in rag.verify.Verifier; this module only adapts HTTP to it.
One database connection is shared and guarded by a lock (Retriever and the check log
use it), so checks run one at a time: enough for a demo, not for real traffic.
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi import Path as PathParam
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from rag.config import load_env
from rag.db import (
    Connection,
    connect,
    corpus_totals,
    corpus_version,
    delete_checks,
    list_sources,
    share_check,
    shared_check,
    unshare_check,
)
from rag.ratelimit import RateLimiter
from rag.embed import DEFAULT_EMBEDDER, Embedder, get_embedder
from rag.search import Retriever
from rag.timeutil import WARSAW, parse_ts
from rag.verify import MSG_OUTAGE, CheckResult, Verifier, checks_retention_days

log = logging.getLogger(__name__)

# Fields that are internal diagnostics and never reach the user.
_INTERNAL_RESULT = ("error",)
_INTERNAL_CLAIM = ("error", "shortlist_ids", "invented_ids", "live")  # live: lookup log, the note says it to the user
# check_id is a UUID4 (rag/verify.py); anything else is rejected before touching the database.
CHECK_ID_PATTERN = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
SHARE_ID_PATTERN = r"^[A-Za-z0-9_-]{16}$"  # secrets.token_urlsafe(12)
# Peers whose X-Forwarded-For is trusted (the Bun frontend proxy and the Discord bot run locally).
DEFAULT_TRUSTED_PROXIES = "127.0.0.1,::1"
# Hard cap on the request body; the Verifier answers `too_long` above its own limit.
MAX_REQUEST_CHARS = 50_000


class VerifyRequest(BaseModel):
    text: str = Field(max_length=MAX_REQUEST_CHARS)
    now: str | None = Field(None, description="Request time, ISO 8601 (demos on an old snapshot)")


def public_result(res: CheckResult) -> dict[str, Any]:
    return public_dict(res.to_dict())


def public_dict(out: dict[str, Any]) -> dict[str, Any]:
    """A CheckResult as a dict (fresh or from the check log) without the internal fields."""
    for k in _INTERNAL_RESULT:
        out.pop(k, None)
    for c in out["claims"]:
        for k in _INTERNAL_CLAIM:
            c.pop(k, None)
    return out


@dataclass
class Services:
    conn: Connection
    embedder: Embedder
    llm_factory: Callable[[], Any]
    owns_conn: bool = True  # False when the caller (a test) closes the connection itself
    lock: threading.Lock = field(default_factory=threading.Lock)
    # Checks cost LLM calls: per-client and daily limits (rag/ratelimit.py, PRAWDOWID_RATE_*).
    limiter: RateLimiter = field(default_factory=RateLimiter.from_env)
    # Writes that cost nothing but touch the database (share, delete): a looser per-client brake.
    write_limiter: RateLimiter = field(default_factory=lambda: RateLimiter(20, 200, 0))
    trusted_proxies: frozenset[str] = field(default_factory=lambda: frozenset(
        p.strip() for p in (os.environ.get("PRAWDOWID_TRUSTED_PROXIES") or DEFAULT_TRUSTED_PROXIES).split(",")
        if p.strip()
    ))
    _verifier: Verifier | None = None

    def verifier(self) -> Verifier:
        if self._verifier is None:
            self._verifier = Verifier(Retriever(self.conn, self.embedder), self.llm_factory())
        return self._verifier


def _default_services() -> Services:
    from rag.llm import make_llm

    load_env()
    embedder = get_embedder(os.environ.get("PRAWDOWID_EMBEDDER") or DEFAULT_EMBEDDER)
    return Services(conn=connect(), embedder=embedder, llm_factory=make_llm)


def create_app(services: Callable[[], Services] = _default_services) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.svc = services()
        yield
        if app.state.svc.owns_conn:
            app.state.svc.conn.close()

    app = FastAPI(title="Prawdowid API", lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json")

    def svc() -> Services:
        return app.state.svc

    def client_key(request: Request) -> str:
        """Client for rate limiting: the peer address, or the last X-Forwarded-For entry when the
        peer is a trusted local proxy (Bun sets it to the visitor's address, the bot to its user)."""
        peer = request.client.host if request.client else "unknown"
        fwd = request.headers.get("x-forwarded-for", "")
        if peer in svc().trusted_proxies and fwd.strip():
            return fwd.split(",")[-1].strip()[:200]
        return peer

    def limit(limiter: RateLimiter, request: Request) -> None:
        refused = limiter.hit(client_key(request))
        if refused:
            log.warning("rate limit (%s) for a client, retry after %d s", refused.scope, refused.retry_after)
            raise HTTPException(429, refused.message, headers={"Retry-After": str(refused.retry_after)})

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        s = svc()
        with s.lock:
            return {"status": "ok", "corpus_version": corpus_version(s.conn)}

    @app.post("/api/verify")
    def verify(req: VerifyRequest, request: Request) -> dict[str, Any]:
        now: datetime | None = None
        if req.now:
            now = parse_ts(req.now, naive_tz=WARSAW)
            if now is None:
                raise HTTPException(422, "now: oczekiwano czasu w formacie ISO 8601")
        s = svc()
        limit(s.limiter, request)
        with s.lock:
            try:
                verifier = s.verifier()
            except (Exception, SystemExit) as e:  # make_llm exits when no API key is set
                log.error("cannot create LLM client: %s", e)
                raise HTTPException(503, MSG_OUTAGE) from None
            res = verifier.verify(req.text, now)
        if res.error:
            log.warning("check %s: %s", res.check_id, res.error)
        return public_result(res)

    @app.delete("/api/checks/{check_id}")
    def delete_check(request: Request, check_id: str = PathParam(pattern=CHECK_ID_PATTERN)) -> dict[str, Any]:
        """Delete the log of one check (and its public share). The random check_id (UUID4) returned
        with the result is the only key to it, so only whoever received the result can delete it."""
        s = svc()
        limit(s.write_limiter, request)
        with s.lock:
            if not delete_checks(s.conn, [check_id]):
                raise HTTPException(404, "Nie ma zapisu o tym identyfikatorze (mógł już zostać usunięty).")
        return {"deleted": True, "check_id": check_id}

    def expires(created_at: str | None) -> str | None:
        days = checks_retention_days()
        start = parse_ts(created_at)
        return (start + timedelta(days=days)).isoformat().replace("+00:00", "Z") if start and days else None

    @app.post("/api/checks/{check_id}/share")
    def share(request: Request, check_id: str = PathParam(pattern=CHECK_ID_PATTERN)) -> dict[str, Any]:
        """Publish a read-only copy of the result at /wynik/<share_id>: the result as logged at check
        time (personal data masked), never editable. A separate random key, so the public link
        cannot delete or unshare the check. Lives as long as the check log (retention)."""
        s = svc()
        limit(s.write_limiter, request)
        with s.lock:
            row = share_check(s.conn, check_id)
        if row is None:
            raise HTTPException(404, "Nie ma zapisu tego sprawdzenia, więc nie można go udostępnić.")
        return {"share_id": row["share_id"], "path": f"/wynik/{row['share_id']}",
                "expires_at": expires(row["created_at"])}

    @app.delete("/api/checks/{check_id}/share")
    def unshare(request: Request, check_id: str = PathParam(pattern=CHECK_ID_PATTERN)) -> dict[str, Any]:
        s = svc()
        limit(s.write_limiter, request)
        with s.lock:
            if not unshare_check(s.conn, check_id):
                raise HTTPException(404, "Nie ma zapisu tego sprawdzenia.")
        return {"shared": False, "check_id": check_id}

    @app.get("/api/shares/{share_id}")
    def shared(share_id: str = PathParam(pattern=SHARE_ID_PATTERN)) -> JSONResponse:
        s = svc()
        with s.lock:
            row = shared_check(s.conn, share_id)
        if row is None:
            raise HTTPException(404, "Ten link wygasł albo autor wyłączył udostępnianie.")
        result = public_dict(row["result"])
        result["check_id"] = ""  # the check_id deletes the check; it never leaves through a share
        body = {"share_id": row["share_id"], "checked_at": row["created_at"], "shared_at": row["shared_at"],
                "expires_at": expires(row["created_at"]), "result": result}
        # Shared pages are for the people given the link, not for search engines.
        return JSONResponse(body, headers={"X-Robots-Tag": "noindex", "Cache-Control": "no-store"})

    @app.get("/api/sources")
    def sources() -> dict[str, Any]:
        s = svc()
        with s.lock:
            return {"totals": corpus_totals(s.conn), "sources": list_sources(s.conn)}

    return app


def main() -> None:
    import uvicorn

    load_env()
    logging.basicConfig(level=logging.INFO)
    host = os.environ.get("API_HOST", "127.0.0.1")
    port = int(os.environ.get("API_PORT", "8000"))
    uvicorn.run(create_app(), host=host, port=port)
