"""Zapis danych: documents.jsonl (append-only), current.json, state.json, crawl_log.jsonl."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


def now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def atomic_write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        log.error("Uszkodzony plik %s – zaczynam od pustego stanu (stary zachowany jako .corrupt)", path)
        path.rename(path.with_suffix(path.suffix + ".corrupt"))
        return default


def _append_line(path: Path, obj: dict) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


class Store:
    DOC_FIELDS = (
        "id", "url", "source", "institution", "doc_type", "region", "title", "content",
        "content_hash", "version", "published_at", "fetched_at", "fetch_method",
    )

    def __init__(self, data_dir: Path):
        self.dir = Path(data_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.docs_path = self.dir / "documents.jsonl"
        self.current_path = self.dir / "current.json"
        self.state_path = self.dir / "state.json"
        self.log_path = self.dir / "crawl_log.jsonl"
        self.current: dict[str, dict] = _load_json(self.current_path, {})
        self.state: dict[str, Any] = _load_json(self.state_path, {})
        for k in ("sources", "http_cache", "backoff"):
            self.state.setdefault(k, {})
        self._reconcile_current()

    # ---------- dokumenty ----------
    def _reconcile_current(self) -> None:
        """Jeśli proces padł między dopisaniem linii a zapisem indeksu – odbuduj indeks z JSONL."""
        if not self.docs_path.exists():
            return
        if self.current_path.exists() and self.current_path.stat().st_mtime >= self.docs_path.stat().st_mtime:
            return
        log.warning("current.json starszy niż documents.jsonl – odbudowuję indeks")
        cur: dict[str, dict] = {}
        with self.docs_path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                prev = cur.get(d["url"])
                if prev is None or d["version"] > prev["version"]:
                    cur[d["url"]] = {"version": d["version"], "content_hash": d["content_hash"], "id": d["id"]}
        self.current = cur
        atomic_write_json(self.current_path, cur)

    def save_document(self, *, url: str, source: str, institution: str, doc_type: str | None,
                      region: str | None, title: str | None, content: str, published_at: str | None,
                      fetch_method: str, extra: dict | None = None) -> str:
        """Zwraca 'new' | 'updated' | 'unchanged'. Starych linii nigdy nie modyfikuje."""
        content_hash = sha256(content)
        cur = self.current.get(url)
        if cur and cur["content_hash"] == content_hash:
            return "unchanged"
        version = cur["version"] + 1 if cur else 1
        doc_id = f"{sha256(url)[:16]}-v{version}"
        rec = {
            "id": doc_id, "url": url, "source": source, "institution": institution,
            "doc_type": doc_type, "region": region, "title": title, "content": content,
            "content_hash": content_hash, "version": version, "published_at": published_at,
            "fetched_at": iso(now_utc()), "fetch_method": fetch_method,
        }
        if extra:
            rec.update({k: v for k, v in extra.items() if k not in rec})
        _append_line(self.docs_path, rec)
        self.current[url] = {"version": version, "content_hash": content_hash, "id": doc_id}
        atomic_write_json(self.current_path, self.current)
        return "updated" if cur else "new"

    # ---------- walidatory HTTP ----------
    def validators(self, url: str) -> dict:
        return self.state["http_cache"].get(url, {})

    def set_validators(self, url: str, etag: str | None, last_modified: str | None) -> None:
        if etag or last_modified:
            self.state["http_cache"][url] = {"etag": etag, "last_modified": last_modified}
        else:
            self.state["http_cache"].pop(url, None)

    # ---------- stan źródeł ----------
    def source_state(self, name: str) -> dict:
        st = self.state["sources"].setdefault(name, {})
        st.setdefault("known", {})
        st.setdefault("pending", [])
        st.setdefault("history", [])
        return st

    def save_state(self) -> None:
        atomic_write_json(self.state_path, self.state)

    def append_log(self, entry: dict) -> None:
        _append_line(self.log_path, entry)

    def read_log(self) -> list[dict]:
        if not self.log_path.exists():
            return []
        out = []
        with self.log_path.open(encoding="utf-8") as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        return out

    def count_versions(self) -> int:
        if not self.docs_path.exists():
            return 0
        with self.docs_path.open("rb") as f:
            return sum(1 for line in f if line.strip())
