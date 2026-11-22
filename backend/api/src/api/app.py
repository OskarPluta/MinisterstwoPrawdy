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
from datetime import datetime
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from rag.config import load_env
from rag.db import Connection, connect, corpus_totals, corpus_version, list_sources
from rag.embed import DEFAULT_EMBEDDER, Embedder, get_embedder
from rag.search import Retriever
from rag.timeutil import WARSAW, parse_ts
from rag.verify import MSG_OUTAGE, CheckResult, Verifier

log = logging.getLogger(__name__)

# Fields that are internal diagnostics and never reach the user.
_INTERNAL_RESULT = ("error",)
_INTERNAL_CLAIM = ("error", "shortlist_ids", "invented_ids", "live")  # live: lookup log, the note says it to the user
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
    return out


@dataclass
class Services:
    conn: Connection
    embedder: Embedder
    llm_factory: Callable[[], Any]
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

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        s = svc()
        with s.lock:
            return {"status": "ok", "corpus_version": corpus_version(s.conn)}

    @app.post("/api/verify")
    def verify(req: VerifyRequest) -> dict[str, Any]:
        now: datetime | None = None
        if req.now:
            now = parse_ts(req.now, naive_tz=WARSAW)
            if now is None:
                raise HTTPException(422, "now: oczekiwano czasu w formacie ISO 8601")
        s = svc()
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
