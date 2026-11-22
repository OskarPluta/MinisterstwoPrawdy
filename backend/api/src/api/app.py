"""HTTP API for the web frontend. Routes live under /api (the frontend proxies /api/*).

    GET    /api/health                  database reachable, corpus version, contact
    POST   /api/verify                  {text, now?} -> public view of rag.verify.CheckResult;
                                        links in the text are read as attachments
    POST   /api/verify/upload           multipart: text?, now?, files (images, PDF; up to 3) -> same
    DELETE /api/checks/{check_id}       delete one check log (and its share)
    POST   /api/checks/{check_id}/share read-only copy at /wynik/<share_id>; DELETE unshares
    GET    /api/shares/{share_id}       the shared, read-only result
    GET    /api/sources                 source catalog for audit + corpus totals
    GET    /api/stats                   corpus totals + checks observability stats
    GET    /api/export                  analyst export of the check log (CSV/JSON), bearer PRAWDOWID_ADMIN_TOKEN

Verification logic stays in rag.verify.Verifier and reading material in intake.Intake;
this module only adapts HTTP to them. One database connection is shared and guarded by a
lock (Retriever and the check log use it), so checks run one at a time: enough for a demo,
not for real traffic. Fetching links and reading images run outside the lock.
"""

from __future__ import annotations

import hmac
import logging
import os
import re
import threading
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from fastapi import FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi import Path as PathParam
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from intake import Intake, Upload, make_intake
from intake.core import MAX_FILE_BYTES, MAX_FILES
from rag.attachments import Attachment
from rag.config import load_env
from rag.db import (
    Connection,
    checks_stats,
    connect,
    corpus_totals,
    corpus_version,
    delete_checks,
    list_sources,
    share_check,
    shared_check,
    unshare_check,
)
from rag import export as export_mod
from rag.ratelimit import RateLimiter
from rag.embed import DEFAULT_EMBEDDER, Embedder, get_embedder
from rag.llm import usage_meter
from rag.search import Retriever
from rag.timeutil import WARSAW, parse_ts
from rag.verify import MSG_OUTAGE, CheckResult, Verifier, checks_retention_days, record_failure

log = logging.getLogger(__name__)

# Fields that are internal diagnostics and never reach the user.
_INTERNAL_RESULT = ("error", "error_type", "usage")
_INTERNAL_CLAIM = ("error", "error_type", "shortlist_ids", "invented_ids", "live")  # live: lookup log, the note says it to the user
_INTERNAL_INPUT = ("meta",)
# check_id is a UUID4 (rag/verify.py); anything else is rejected before touching the database.
CHECK_ID_PATTERN = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
SHARE_ID_PATTERN = r"^[A-Za-z0-9_-]{16}$"  # secrets.token_urlsafe(12)
# Peers whose X-Forwarded-For is trusted (the Bun frontend proxy and the Discord bot run locally).
DEFAULT_TRUSTED_PROXIES = "127.0.0.1,::1"
# Hard cap on the request body; the Verifier answers `too_long` above its own limit.
MAX_REQUEST_CHARS = 50_000
# The analyst export is off unless PRAWDOWID_ADMIN_TOKEN holds at least this many characters.
MIN_ADMIN_TOKEN_CHARS = 16


_EMAIL_RX = re.compile(r"^[^@\s<>\"']+@[^@\s<>\"']+\.[A-Za-z]{2,}$")


def public_contact() -> str | None:
    """PRAWDOWID_CONTACT_EMAIL: where publishers ask for removal (shown on /jak-to-dziala)."""
    email = (os.environ.get("PRAWDOWID_CONTACT_EMAIL") or "").strip()
    return email if _EMAIL_RX.match(email) else None


def admin_token_from_env() -> str | None:
    token = (os.environ.get("PRAWDOWID_ADMIN_TOKEN") or "").strip()
    if token and len(token) < MIN_ADMIN_TOKEN_CHARS:
        log.warning("PRAWDOWID_ADMIN_TOKEN is shorter than %d characters; /api/export stays off",
                    MIN_ADMIN_TOKEN_CHARS)
        return None
    return token or None

# Channel recorded in the checks log: the Discord bot in HTTP mode sends X-Prawdowid-Channel.
CHANNELS = {"web", "discord"}


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
    # Logged results from before attachments have no "inputs" (shared pages read them back).
    for i in out.setdefault("inputs", []):
        for k in _INTERNAL_INPUT:
            i.pop(k, None)
    return out


@dataclass
class Services:
    conn: Connection
    embedder: Embedder
    llm_factory: Callable[[], Any]
    intake: Intake = field(default_factory=lambda: Intake(None))
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
    # Bearer token of GET /api/export; None: the endpoint does not exist (404).
    admin_token: str | None = field(default_factory=admin_token_from_env)
    _verifier: Verifier | None = None

    def verifier(self) -> Verifier:
        if self._verifier is None:
            self._verifier = Verifier(Retriever(self.conn, self.embedder), self.llm_factory(), channel="web")
        return self._verifier


def _default_services() -> Services:
    from rag.llm import make_llm

    load_env()
    embedder = get_embedder(os.environ.get("PRAWDOWID_EMBEDDER") or DEFAULT_EMBEDDER)
    return Services(conn=connect(), embedder=embedder, llm_factory=make_llm, intake=make_intake())


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
            return {"status": "ok", "corpus_version": corpus_version(s.conn), "contact": public_contact()}

    def parse_now(value: str | None) -> datetime | None:
        if not value:
            return None
        now = parse_ts(value, naive_tz=WARSAW)
        if now is None:
            raise HTTPException(422, "now: oczekiwano czasu w formacie ISO 8601")
        return now

    def record_failed(text: str, e: BaseException, outcome: str, channel: str) -> None:
        """Log a check that never reached the Verifier (admin panel + alert); never raises."""
        s = svc()
        try:
            with s.lock:
                record_failure(s.conn, text, e, outcome=outcome, channel=channel)
        except Exception as err:  # noqa: BLE001 - e.g. the database itself is down
            log.error("cannot log failed check: %r", err)

    def run_check(
        request: Request, text: str, now: datetime | None, files: list[Upload], channel: str,
    ) -> dict[str, Any]:
        s = svc()
        limit(s.limiter, request)
        with s.lock:
            try:
                verifier = s.verifier()
            except (Exception, SystemExit) as e:  # make_llm exits when no API key is set
                log.error("cannot create LLM client: %s", e)
                verifier = None
                failure: BaseException = e
            else:
                domains = {src["publishing_domain"] for src in list_sources(s.conn)}
        if verifier is None:
            record_failed(text, failure, "config_error", channel)
            raise HTTPException(503, MSG_OUTAGE) from None
        try:
            # One meter for the whole check: OCR during intake counts too (rag/llm.py).
            with usage_meter():
                # Network and OCR without the database lock; the LLM client is thread-safe.
                attachments: list[Attachment] = s.intake.collect(
                    text, files, transcriber=verifier.llm, known_domains=lambda: domains,
                )
                with s.lock:
                    res = verifier.verify(text, now, attachments, channel=channel)
        except Exception as e:  # noqa: BLE001 - logged for the admin, the user sees an outage
            log.exception("check failed outside the verifier")
            record_failed(text, e, "internal_error", channel)
            raise HTTPException(500, MSG_OUTAGE) from None
        if res.error:
            log.warning("check %s: %s", res.check_id, res.error)
        out = public_result(res)
        if not verifier.record:  # nothing was logged (retention 0): no key to share or delete
            out["check_id"] = ""
        return out

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

    @app.get("/api/export", include_in_schema=False)
    def export(
        request: Request,
        fmt: str = Query("json", alias="format", pattern="^(json|csv)$"),
        since: str | None = Query(None, description="ISO 8601, inclusive; no offset = Europe/Warsaw"),
        until: str | None = Query(None, description="ISO 8601, exclusive; no offset = Europe/Warsaw"),
        status: list[str] = Query([], description="Check status; repeatable or comma-separated"),
        verdict: list[str] = Query([], description="Keep only claims with this verdict; repeatable or comma-separated"),
    ) -> Response:
        """Analyst export of the check log (rag/export.py): masked, without the user's message and
        without deletion keys. Only for the holder of PRAWDOWID_ADMIN_TOKEN (Authorization: Bearer);
        without the variable the route answers 404, as if it did not exist."""
        s = svc()
        if not s.admin_token:
            raise HTTPException(404, "Not Found")
        limit(s.write_limiter, request)  # failed attempts count too
        scheme, _, given = request.headers.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(
            given.strip().encode("utf-8"), s.admin_token.encode("utf-8")
        ):
            raise HTTPException(401, "Wymagany token administratora.", headers={"WWW-Authenticate": "Bearer"})
        try:
            filters = export_mod.ExportFilters.parse(since, until, status, verdict)
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
        with s.lock:
            records = export_mod.load(s.conn, filters)
        if fmt == "csv":
            body, media = export_mod.to_csv(records), "text/csv; charset=utf-8"
        else:
            body, media = export_mod.to_json(records, filters), "application/json; charset=utf-8"
        log.info("analyst export: %d checks, format %s", len(records), fmt)
        return Response(body.encode("utf-8"), media_type=media, headers={
            "Content-Disposition": f'attachment; filename="{export_mod.filename(fmt)}"',
            "Cache-Control": "no-store",
            "X-Robots-Tag": "noindex",
            "X-Content-Type-Options": "nosniff",
        })

    def channel_of(value: str | None) -> str:
        return value if value in CHANNELS else "web"

    @app.post("/api/verify")
    def verify(
        req: VerifyRequest, request: Request,
        x_prawdowid_channel: str | None = Header(None, include_in_schema=False),
    ) -> dict[str, Any]:
        return run_check(request, req.text, parse_now(req.now), [], channel_of(x_prawdowid_channel))

    @app.post("/api/verify/upload")
    def verify_upload(
        request: Request, text: str = Form(""), now: str | None = Form(None), files: list[UploadFile] = File(default=[]),
        x_prawdowid_channel: str | None = Header(None, include_in_schema=False),
    ) -> dict[str, Any]:
        if len(text) > MAX_REQUEST_CHARS:
            raise HTTPException(422, "text: wiadomość jest za długa")
        files = [f for f in files if f.filename or f.size]
        if len(files) > MAX_FILES:
            raise HTTPException(422, f"Możesz dodać najwyżej {MAX_FILES} pliki.")
        if not text.strip() and not files:
            raise HTTPException(422, "Wpisz treść wiadomości albo dodaj plik.")
        uploads = []
        for f in files:
            data = f.file.read(MAX_FILE_BYTES + 1)
            if len(data) > MAX_FILE_BYTES:
                raise HTTPException(413, f"Plik jest za duży (limit {MAX_FILE_BYTES // 1_000_000} MB).")
            if not data:
                raise HTTPException(422, "Jeden z plików jest pusty.")
            uploads.append(Upload(f.filename or "plik", data))
        return run_check(request, text, parse_now(now), uploads, channel_of(x_prawdowid_channel))

    @app.get("/api/sources")
    def sources() -> dict[str, Any]:
        s = svc()
        with s.lock:
            return {"totals": corpus_totals(s.conn), "sources": list_sources(s.conn)}

    @app.get("/api/stats")
    def stats() -> dict[str, Any]:
        s = svc()
        with s.lock:
            return {"corpus": corpus_totals(s.conn), "checks": checks_stats(s.conn)}

    return app


def main() -> None:
    import uvicorn

    load_env()
    logging.basicConfig(level=logging.INFO)
    host = os.environ.get("API_HOST", "127.0.0.1")
    port = int(os.environ.get("API_PORT", "8000"))
    uvicorn.run(create_app(), host=host, port=port)
