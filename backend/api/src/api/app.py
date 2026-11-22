"""HTTP API for the web frontend. Routes live under /api (the frontend proxies /api/*).

    GET  /api/health         database reachable, corpus version
    POST /api/verify         {text, now?} -> public view of rag.verify.CheckResult;
                             links in the text are read as attachments
    POST /api/verify/upload  multipart: text?, now?, files (images, PDF; up to 3) -> same
    GET  /api/sources        source catalog for audit + corpus totals
    GET  /api/stats          corpus totals + checks observability stats

Verification logic stays in rag.verify.Verifier and reading material in intake.Intake;
this module only adapts HTTP to them. One database connection is shared and guarded by a
lock (Retriever and the check log use it), so checks run one at a time: enough for a demo,
not for real traffic. Fetching links and reading images run outside the lock.
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from intake import Intake, Upload, make_intake
from intake.core import MAX_FILE_BYTES, MAX_FILES
from rag.attachments import Attachment
from rag.config import load_env
from rag.db import Connection, checks_stats, connect, corpus_totals, corpus_version, list_sources
from rag.embed import DEFAULT_EMBEDDER, Embedder, get_embedder
from rag.search import Retriever
from rag.timeutil import WARSAW, parse_ts
from rag.verify import MSG_OUTAGE, CheckResult, Verifier

log = logging.getLogger(__name__)

# Fields that are internal diagnostics and never reach the user.
_INTERNAL_RESULT = ("error", "usage")
_INTERNAL_CLAIM = ("error", "shortlist_ids", "invented_ids", "live")  # live: lookup log, the note says it to the user
_INTERNAL_INPUT = ("meta",)
# Hard cap on the request body; the Verifier answers `too_long` above its own limit.
MAX_REQUEST_CHARS = 50_000


class VerifyRequest(BaseModel):
    text: str = Field(max_length=MAX_REQUEST_CHARS)
    now: str | None = Field(None, description="Request time, ISO 8601 (demos on an old snapshot)")


def public_result(res: CheckResult) -> dict[str, Any]:
    out = res.to_dict()
    for k in _INTERNAL_RESULT:
        out.pop(k, None)
    for c in out["claims"]:
        for k in _INTERNAL_CLAIM:
            c.pop(k, None)
    for i in out["inputs"]:
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
    _verifier: Verifier | None = None

    def verifier(self) -> Verifier:
        if self._verifier is None:
            self._verifier = Verifier(Retriever(self.conn, self.embedder), self.llm_factory())
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

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        s = svc()
        with s.lock:
            return {"status": "ok", "corpus_version": corpus_version(s.conn)}

    def parse_now(value: str | None) -> datetime | None:
        if not value:
            return None
        now = parse_ts(value, naive_tz=WARSAW)
        if now is None:
            raise HTTPException(422, "now: oczekiwano czasu w formacie ISO 8601")
        return now

    def run_check(text: str, now: datetime | None, files: list[Upload]) -> dict[str, Any]:
        s = svc()
        with s.lock:
            try:
                verifier = s.verifier()
            except (Exception, SystemExit) as e:  # make_llm exits when no API key is set
                log.error("cannot create LLM client: %s", e)
                raise HTTPException(503, MSG_OUTAGE) from None
            domains = {src["publishing_domain"] for src in list_sources(s.conn)}
        # Network and OCR without the database lock; the LLM client is thread-safe.
        attachments: list[Attachment] = s.intake.collect(
            text, files, transcriber=verifier.llm, known_domains=lambda: domains,
        )
        with s.lock:
            res = verifier.verify(text, now, attachments)
        if res.error:
            log.warning("check %s: %s", res.check_id, res.error)
        return public_result(res)

    @app.post("/api/verify")
    def verify(req: VerifyRequest) -> dict[str, Any]:
        return run_check(req.text, parse_now(req.now), [])

    @app.post("/api/verify/upload")
    def verify_upload(
        text: str = Form(""), now: str | None = Form(None), files: list[UploadFile] = File(default=[]),
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
        return run_check(text, parse_now(now), uploads)

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
