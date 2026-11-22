"""Admin panel API: a separate process from the public API, under /admin/api.

    POST /admin/api/login, /logout; GET /me        shared password, signed session cookie
    GET  /admin/api/meta                           outcome categories, source types, channels
    GET  /admin/api/overview                       last 24 h / 7 days, daily series, alerts, sources
    GET  /admin/api/checks[?filters], /checks/{id} message history (masked user text only)
    PUT|DELETE /admin/api/checks/{id}/review       manual category and note
    GET  /admin/api/failures?from&to               checks without a verdict, by category
    GET  /admin/api/usage?from&to&group            LLM calls, tokens, cost per currency
    GET|POST /admin/api/sources, PATCH /sources/{id}, GET|POST /sources/{id}/documents
    POST /admin/api/documents/{id}/withdraw|restore
    GET|POST /admin/api/trusted-domains, DELETE /trusted-domains/{domain}
    GET  /admin/api/alerts, /alerts/count; POST /alerts/{id}/ack, /alerts/ack-all
    GET  /admin/api/audit

The public frontend proxies only /api/* to the public API, and these routes exist only in
this process, which listens on 127.0.0.1:8001 by default. All logic stays in rag (admin_db,
outcomes, alerts); this module adapts HTTP to it and writes the audit log.
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from rag import admin_db, alerts
from rag.config import load_env
from rag.db import Connection, connect
from rag.embed import DEFAULT_EMBEDDER, Embedder, get_embedder
from rag.outcomes import FAILURE_OUTCOMES, GROUP_OF, NO_RESULT_OUTCOMES, OUTCOME_PL, DEGRADED_OUTCOMES

from . import links, reports
from .auth import COOKIE, MSG_LOGIN, MSG_THROTTLED, AuthConfig, LoginThrottle, client_key, require_session
from .notifier import Notifier, NotifierConfig

log = logging.getLogger(__name__)

CHANNELS = ("web", "discord", "cli", "eval")
LIST_PREVIEW_CHARS = 280
GROUP_OUTCOMES = {"failure": sorted(FAILURE_OUTCOMES), "degraded": sorted(DEGRADED_OUTCOMES),
                  "no_result": sorted(NO_RESULT_OUTCOMES), "success": ["verdict"]}


@dataclass
class Services:
    conn: Connection
    embedder_factory: Callable[[], Embedder | None]
    fetcher_factory: Callable[[], Any]
    auth: AuthConfig
    notifier: Notifier | None = None
    conn_factory: Callable[[], Connection] | None = None  # the notifier thread's own connection
    owns_conn: bool = True
    lock: threading.Lock = field(default_factory=threading.Lock)
    throttle: LoginThrottle = field(default_factory=LoginThrottle)
    _embedder: Embedder | None = None
    _fetcher: Any = None
    _fetcher_made: bool = False

    def embedder(self) -> Embedder | None:
        """Loaded on the first added link: the same model the public API searches with."""
        if self._embedder is None:
            self._embedder = self.embedder_factory()
        return self._embedder

    def fetcher(self) -> Any:
        if not self._fetcher_made:
            self._fetcher, self._fetcher_made = self.fetcher_factory(), True
        return self._fetcher


def _default_services() -> Services:
    from intake.fetch import SafeFetcher, contact_from_env, user_agent

    load_env()
    auth = AuthConfig.from_env()

    def fetcher() -> Any:
        contact = contact_from_env()
        return SafeFetcher(user_agent(contact)) if contact else None

    notifier = None if os.environ.get("ADMIN_NOTIFIER") == "0" else Notifier(NotifierConfig.from_env())
    return Services(
        conn=connect(), auth=auth, notifier=notifier, conn_factory=connect, fetcher_factory=fetcher,
        embedder_factory=lambda: get_embedder(os.environ.get("PRAWDOWID_EMBEDDER") or DEFAULT_EMBEDDER),
    )


# ---- request bodies ------------------------------------------------------------------------

class LoginBody(BaseModel):
    password: str = Field(max_length=500)


class ReviewBody(BaseModel):
    label: str = Field(min_length=1, max_length=80)
    note: str | None = Field(None, max_length=2000)


class SourceUpdate(BaseModel):
    enabled: bool
    publisher: str | None = Field(None, max_length=200)
    source_type: str | None = None
    note: str | None = Field(None, max_length=2000)


class SourceCreate(BaseModel):
    source_id: str = Field(max_length=80)
    publisher: str = Field(max_length=200)
    domain: str = Field(max_length=300)
    source_type: str
    note: str | None = Field(None, max_length=2000)


class LinkBody(BaseModel):
    url: str = Field(max_length=2000)


class NoteBody(BaseModel):
    note: str | None = Field(None, max_length=2000)


class DomainBody(BaseModel):
    domain: str = Field(max_length=300)
    note: str | None = Field(None, max_length=500)
    enabled: bool = True


# ---- helpers ---------------------------------------------------------------------------------

def _check_view(row: dict[str, Any], preview: bool = False) -> dict[str, Any]:
    out = {k: v for k, v in row.items() if k != "result"}
    if preview and len(out.get("input_masked") or "") > LIST_PREVIEW_CHARS:
        out["input_masked"] = out["input_masked"][:LIST_PREVIEW_CHARS] + "…"
    o = out.get("outcome") or ""
    out["outcome_pl"] = OUTCOME_PL.get(o, o or "—")
    out["group"] = GROUP_OF.get(o, "failure" if out.get("status") == "error" else "no_result")
    return out


def _range(start: str | None, end: str | None):
    try:
        return reports.day_range(start, end)
    except ValueError as e:
        raise HTTPException(422, str(e)) from None


def create_app(services: Callable[[], Services] = _default_services) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        s = services()
        app.state.svc = s
        if s.notifier is not None and s.conn_factory is not None:
            s.notifier.start(s.conn_factory)
        yield
        if s.notifier is not None:
            s.notifier.stop()
        if s.owns_conn:
            s.conn.close()

    app = FastAPI(title="Prawdowid Admin API", lifespan=lifespan, docs_url="/admin/api/docs",
                  openapi_url="/admin/api/openapi.json")

    def svc() -> Services:
        return app.state.svc

    def session(request: Request) -> int:
        return require_session(request, svc().auth)

    def audit(action: str, target: str | None, details: dict[str, Any] | None = None) -> None:
        admin_db.audit(svc().conn, action, target, details)

    # ---- session (no login required) -----------------------------------------------------
    @app.post("/admin/api/login", status_code=204)
    def login(body: LoginBody, request: Request, response: Response) -> None:
        s = svc()
        key = client_key(request)
        if s.throttle.blocked(key):
            raise HTTPException(429, MSG_THROTTLED)
        if not s.auth.password_ok(body.password):
            s.throttle.failed(key)
            with s.lock:
                audit("login.failed", None)
            raise HTTPException(401, MSG_LOGIN)
        s.throttle.reset(key)
        response.set_cookie(COOKIE, s.auth.make_token(), max_age=s.auth.ttl_s, httponly=True,
                            samesite="strict", secure=s.auth.cookie_secure, path="/admin")
        with s.lock:
            audit("login", None)

    @app.post("/admin/api/logout", status_code=204)
    def logout(response: Response) -> None:
        response.delete_cookie(COOKIE, path="/admin")

    api = APIRouter(prefix="/admin/api", dependencies=[Depends(session)])

    @api.get("/me")
    def me(expiry: int = Depends(session)) -> dict[str, Any]:
        from datetime import datetime, timezone

        return {"ok": True, "expires_at": reports.iso(datetime.fromtimestamp(expiry, timezone.utc))}

    @api.get("/meta")
    def meta() -> dict[str, Any]:
        return {"outcomes": reports.outcome_catalog(), "groups": GROUP_OUTCOMES,
                "source_types": list(admin_db.SOURCE_TYPES), "channels": list(CHANNELS)}

    @api.get("/overview")
    def overview() -> dict[str, Any]:
        s = svc()
        with s.lock:
            return reports.overview(s.conn)

    # ---- history -------------------------------------------------------------------------
    @api.get("/checks")
    def checks(
        cursor: str | None = None, limit: int = Query(50, ge=1, le=200), channel: str | None = None,
        status: str | None = None, outcome: list[str] | None = Query(None), group: str | None = None,
        q: str | None = Query(None, max_length=200), start: str | None = Query(None, alias="from"),
        end: str | None = Query(None, alias="to"), unreviewed: bool = False,
    ) -> dict[str, Any]:
        outcomes = list(outcome or [])
        if group:
            if group not in GROUP_OUTCOMES:
                raise HTTPException(422, "group: success | no_result | degraded | failure")
            outcomes += GROUP_OUTCOMES[group]
        since = until = None
        if start or end:
            since, until = reports.utc_bounds(*_range(start, end))
        s = svc()
        with s.lock:
            rows, nxt = admin_db.checks_page(
                s.conn, limit=limit, cursor=cursor, since=since, until=until, channel=channel or None,
                status=status or None, outcomes=outcomes or None, q=q, unreviewed=unreviewed,
            )
        return {"items": [_check_view(r, preview=True) for r in rows], "next": nxt}

    @api.get("/checks/{check_id}")
    def check(check_id: str) -> dict[str, Any]:
        s = svc()
        with s.lock:
            row = admin_db.get_check(s.conn, check_id)
        if row is None:
            raise HTTPException(404, "Nie ma takiego sprawdzenia.")
        out = _check_view(row)
        out["result"] = row["result"]
        out["review"] = ({"label": row["review_label"], "note": row["review_note"], "reviewed_at": row["reviewed_at"]}
                         if row["review_label"] else None)
        for k in ("review_label", "review_note", "reviewed_at"):
            out.pop(k, None)
        return out

    @api.put("/checks/{check_id}/review")
    def review(check_id: str, body: ReviewBody) -> dict[str, Any]:
        s = svc()
        with s.lock:
            if admin_db.get_check(s.conn, check_id) is None:
                raise HTTPException(404, "Nie ma takiego sprawdzenia.")
            admin_db.set_review(s.conn, check_id, body.label.strip(), (body.note or "").strip() or None)
            audit("check.review", check_id, {"label": body.label})
        return {"label": body.label.strip(), "note": (body.note or "").strip() or None}

    @api.delete("/checks/{check_id}/review", status_code=204)
    def unreview(check_id: str) -> None:
        s = svc()
        with s.lock:
            admin_db.delete_review(s.conn, check_id)
            audit("check.review_removed", check_id)

    @api.get("/failures")
    def failures(start: str | None = Query(None, alias="from"), end: str | None = Query(None, alias="to")) -> dict[str, Any]:
        d_from, d_to = _range(start, end)
        s = svc()
        with s.lock:
            return reports.failures(s.conn, d_from, d_to)

    @api.get("/usage")
    def usage(start: str | None = Query(None, alias="from"), end: str | None = Query(None, alias="to"),
              group: str = "day") -> dict[str, Any]:
        d_from, d_to = _range(start, end)
        if group not in reports.USAGE_GROUPS:
            raise HTTPException(422, f"group: {' | '.join(reports.USAGE_GROUPS)}")
        s = svc()
        with s.lock:
            return reports.usage(s.conn, d_from, d_to, group)

    # ---- sources -------------------------------------------------------------------------
    @api.get("/sources")
    def sources() -> dict[str, Any]:
        s = svc()
        with s.lock:
            return {"items": admin_db.admin_sources(s.conn)}

    @api.patch("/sources/{source_id}")
    def update_source(source_id: str, body: SourceUpdate) -> dict[str, Any]:
        s = svc()
        with s.lock:
            try:
                before = admin_db.update_source(s.conn, source_id, enabled=body.enabled, publisher=body.publisher,
                                                source_type=body.source_type, note=body.note)
            except KeyError:
                raise HTTPException(404, "Nie ma takiego źródła.") from None
            except ValueError as e:
                raise HTTPException(422, str(e)) from None
            audit("source.update", source_id, {
                "before": {k: before[k] for k in ("enabled", "admin_publisher", "admin_source_type", "admin_note")},
                "after": body.model_dump(),
            })
            return admin_db.get_source(s.conn, source_id) or {}

    @api.post("/sources", status_code=201)
    def create_source(body: SourceCreate) -> dict[str, Any]:
        s = svc()
        with s.lock:
            try:
                row = admin_db.create_source(s.conn, source_id=body.source_id.strip(), publisher=body.publisher,
                                             domain=body.domain, source_type=body.source_type, note=body.note)
            except ValueError as e:
                raise HTTPException(422, str(e)) from None
            audit("source.create", row["source_id"], {"domain": row["publishing_domain"],
                                                      "source_type": row["source_type"]})
            return row

    @api.get("/sources/{source_id}/documents")
    def documents(source_id: str) -> dict[str, Any]:
        s = svc()
        with s.lock:
            src = admin_db.get_source(s.conn, source_id)
            if src is None:
                raise HTTPException(404, "Nie ma takiego źródła.")
            return {"source": src, "items": admin_db.source_documents(s.conn, source_id)}

    @api.post("/sources/{source_id}/documents", status_code=201)
    def add_document(source_id: str, body: LinkBody) -> dict[str, Any]:
        s = svc()
        with s.lock:
            src = admin_db.get_source(s.conn, source_id)
        if src is None:
            raise HTTPException(404, "Nie ma takiego źródła.")
        try:
            # Network (robots.txt, host spacing) without the database lock.
            record = links.fetch_record(s.fetcher(), src, body.url)
            embedder = s.embedder()
            with s.lock:
                out = links.store_record(s.conn, record, embedder)
                audit("document.add", source_id, {"url": out["citation_url"], "action": out["action"],
                                                  "evidence_id": out["evidence_id"]})
        except links.LinkError as e:
            raise HTTPException(422, e.message) from None
        return out

    @api.post("/documents/{evidence_id}/withdraw")
    def withdraw(evidence_id: int, body: NoteBody) -> dict[str, Any]:
        s = svc()
        with s.lock:
            ev = admin_db.withdraw_evidence(s.conn, evidence_id, body.note)
            if ev is None:
                raise HTTPException(404, "Nie ma takiego dokumentu.")
            audit("document.withdraw", ev["source_id"], {"evidence_id": evidence_id, "url": ev["citation_url"],
                                                         "note": body.note})
            return ev

    @api.post("/documents/{evidence_id}/restore")
    def restore(evidence_id: int) -> dict[str, Any]:
        s = svc()
        with s.lock:
            ev = admin_db.restore_evidence(s.conn, evidence_id)
            if ev is None:
                raise HTTPException(404, "Ten dokument nie jest wycofany.")
            audit("document.restore", ev["source_id"], {"evidence_id": ev["evidence_id"], "url": ev["citation_url"]})
            return ev

    # ---- trusted fact-check domains --------------------------------------------------------
    @api.get("/trusted-domains")
    def domains() -> dict[str, Any]:
        s = svc()
        with s.lock:
            return {"items": admin_db.list_trusted_domains(s.conn)}

    @api.post("/trusted-domains", status_code=201)
    def add_domain(body: DomainBody) -> dict[str, Any]:
        s = svc()
        with s.lock:
            try:
                d = admin_db.add_trusted_domain(s.conn, body.domain, body.note, body.enabled)
            except ValueError as e:
                raise HTTPException(422, str(e)) from None
            audit("domain.upsert", d, {"enabled": body.enabled, "note": body.note})
            return {"domain": d}

    @api.delete("/trusted-domains/{domain}", status_code=204)
    def delete_domain(domain: str) -> None:
        s = svc()
        with s.lock:
            if not admin_db.delete_trusted_domain(s.conn, domain):
                raise HTTPException(404, "Nie ma takiej domeny.")
            audit("domain.delete", domain)

    # ---- alerts and audit --------------------------------------------------------------------
    @api.get("/alerts")
    def list_alerts(open: bool = False, limit: int = Query(200, ge=1, le=1000)) -> dict[str, Any]:
        s = svc()
        with s.lock:
            return {"items": [alerts.alert_payload(a) for a in alerts.list_alerts(s.conn, open_only=open, limit=limit)],
                    "counts": alerts.open_count(s.conn)}

    @api.get("/alerts/count")
    def alert_count() -> dict[str, int]:
        s = svc()
        with s.lock:
            return alerts.open_count(s.conn)

    @api.post("/alerts/{alert_id}/ack")
    def ack(alert_id: int) -> dict[str, int]:
        s = svc()
        with s.lock:
            if not alerts.acknowledge(s.conn, alert_id):
                raise HTTPException(404, "Nie ma takiego otwartego alertu.")
            audit("alert.ack", str(alert_id))
            return alerts.open_count(s.conn)

    @api.post("/alerts/ack-all")
    def ack_all() -> dict[str, int]:
        s = svc()
        with s.lock:
            n = alerts.acknowledge_all(s.conn)
            audit("alert.ack_all", None, {"count": n})
            return alerts.open_count(s.conn)

    @api.get("/audit")
    def audit_log(limit: int = Query(200, ge=1, le=1000)) -> dict[str, Any]:
        s = svc()
        with s.lock:
            return {"items": admin_db.audit_list(s.conn, limit)}

    app.include_router(api)
    return app


def main() -> None:
    import uvicorn

    load_env()
    logging.basicConfig(level=logging.INFO)
    AuthConfig.from_env()  # refuse to start without ADMIN_PASSWORD / ADMIN_SECRET, before binding the port
    host = os.environ.get("ADMIN_HOST", "127.0.0.1")
    port = int(os.environ.get("ADMIN_PORT", "8001"))
    uvicorn.run(create_app(), host=host, port=port)
