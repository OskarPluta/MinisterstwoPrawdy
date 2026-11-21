"""Uruchamianie źródeł, harmonogram (run / once) i raport `status`."""
from __future__ import annotations

import json
import logging
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta

from .adapters import ADAPTERS, Ctx, SourceBlocked, Stats
from .config import Config
from .fetcher import Blocked, Fetcher, RobotsBlocked, RobotsUnavailable, Throttled
from .storage import Store, iso, now_utc, parse_iso

log = logging.getLogger(__name__)
STOP = threading.Event()
HISTORY_LEN = 10


def run_source(ctx: Ctx, src: dict) -> dict:
    name = src["name"]
    st = ctx.store.source_state(name)
    stats = Stats()
    started = now_utc()
    log.info("▶ %s (%s)", name, src["type"])
    try:
        ADAPTERS[src["type"]](ctx, src, st, stats)
    except Throttled as e:
        stats.status = "throttled"
        stats.msg(str(e))
    except RobotsUnavailable as e:
        stats.status = "throttled"
        stats.msg(f"{e} – zgodnie z RFC 9309 wstrzymano pobieranie")
    except (Blocked, SourceBlocked) as e:
        stats.status = "blocked"
        stats.msg(str(e))
    except RobotsBlocked as e:
        stats.status = "blocked"
        stats.msg(f"{e} – nic nie pobieram")
    except Exception as e:
        log.exception("[%s] błąd źródła", name)
        stats.status = "error"
        stats.errors += 1
        stats.msg(f"{type(e).__name__}: {str(e)[:300]}")

    if stats.status is None:
        produced = stats.new + stats.updated + stats.unchanged
        if stats.errors and not produced:
            stats.status = "error"
        elif stats.errors:
            stats.status = "warn"
        elif stats.hint == "not_modified" and not (stats.new or stats.updated):
            stats.status = "not_modified"
        else:
            stats.status = "ok"
    if stats.blocked_urls:
        stats.msg(f"pominięto {len(stats.blocked_urls)} adres(ów) zablokowanych w robots.txt")

    finished = now_utc()
    entry = {
        "source": name, "started_at": iso(started), "finished_at": iso(finished), "status": stats.status,
        "found": stats.found, "new": stats.new, "updated": stats.updated, "unchanged": stats.unchanged,
        "skipped": stats.skipped, "errors": stats.errors, "message": "; ".join(stats.messages)[:2000],
    }
    if stats.blocked_urls:
        entry["blocked_urls"] = stats.blocked_urls[:50]
    ctx.store.append_log(entry)

    st["first_run_at"] = st.get("first_run_at") or iso(started)
    st["last_run"] = iso(started)
    st["last_finished"] = iso(finished)
    st["last_status"] = stats.status
    st["last_message"] = entry["message"]
    if stats.new or stats.updated:
        st["last_new_at"] = iso(finished)
    st["history"] = ([stats.status] + st.get("history", []))[:HISTORY_LEN]
    ctx.store.save_state()
    log.info("■ %s: %s (znaleziono %d, nowe %d, zmienione %d, bez zmian %d, pominięte %d, błędy %d) %s",
             name, stats.status, stats.found, stats.new, stats.updated, stats.unchanged, stats.skipped,
             stats.errors, entry["message"][:300])
    return entry


def next_due(store: Store, src: dict) -> datetime:
    st = store.state["sources"].get(src["name"], {})
    last = parse_iso(st.get("last_run"))
    if last is None:
        return now_utc() - timedelta(seconds=1)
    return last + timedelta(minutes=float(src["interval_minutes"]))


def run_once(ctx: Ctx, sources: list[dict]) -> list[dict]:
    out = []
    for src in sources:
        if STOP.is_set():
            log.info("Zatrzymano – pomijam pozostałe źródła")
            break
        out.append(run_source(ctx, src))
    return out


def run_loop(ctx: Ctx, sources: list[dict]) -> None:
    log.info("Tryb ciągły: %d źródeł. Ctrl+C kończy po bieżącym źródle.", len(sources))
    while not STOP.is_set():
        now = now_utc()
        due = sorted((next_due(ctx.store, s), s["name"], s) for s in sources if next_due(ctx.store, s) <= now)
        if due:
            run_source(ctx, due[0][2])  # najbardziej zaległe najpierw; po każdym ponowne sprawdzenie
            continue
        wake = min(next_due(ctx.store, s) for s in sources)
        log.debug("Następne źródło o %s", wake.isoformat())
        while not STOP.is_set() and now_utc() < wake:
            time.sleep(1)
    log.info("Koniec pracy.")


# ---------------- status ----------------
def _fmt(ts: str | None) -> str:
    dt = parse_iso(ts)
    if not dt:
        return "—"
    return dt.astimezone().strftime("%Y-%m-%d %H:%M")


def status_report(cfg: Config, store: Store) -> str:
    logs = store.read_log()
    by_source: dict[str, list[dict]] = defaultdict(list)
    for e in logs:
        by_source[e["source"]].append(e)
    docs_per_source: dict[str, set] = defaultdict(set)
    vers_per_source: dict[str, int] = defaultdict(int)
    if store.docs_path.exists():
        with store.docs_path.open(encoding="utf-8") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                docs_per_source[d["source"]].add(d["url"])
                vers_per_source[d["source"]] += 1

    rows = []
    now = now_utc()
    for src in cfg.sources:
        name = src["name"]
        st = store.state["sources"].get(name, {})
        alarms = []
        status = st.get("last_status", "—")
        if status in ("blocked", "throttled"):
            alarms.append(status.upper())
        recent = [e["status"] for e in by_source.get(name, [])[-3:]]
        if len(recent) == 3 and all(s == "error" for s in recent):
            alarms.append("3× błąd")
        silence = src.get("silence_alert_hours")
        if silence and st.get("last_run"):
            ref = parse_iso(st.get("last_new_at")) or parse_iso(st.get("first_run_at"))
            if ref and now - ref > timedelta(hours=float(silence)):
                alarms.append(f"cisza >{silence}h")
        if not src.get("enabled", True):
            alarms.append("wyłączone")
        rows.append([name, src["type"], _fmt(st.get("last_run")), status, _fmt(st.get("last_new_at")),
                     f"{len(docs_per_source.get(name, ()))}/{vers_per_source.get(name, 0)}", ", ".join(alarms)])

    headers = ["źródło", "typ", "ostatnio", "status", "ostatnio nowe", "dok/wer", "alarmy"]
    widths = [max(len(str(r[i])) for r in rows + [headers]) for i in range(len(headers))]
    line = "  ".join(h.ljust(w) for h, w in zip(headers, widths))
    out = [line, "-" * len(line)]
    for r in rows:
        out.append("  ".join(str(c).ljust(w) for c, w in zip(r, widths)))
    backoffs = {h: b for h, b in store.state.get("backoff", {}).items()
                if (parse_iso(b.get("until")) or now) > now}
    if backoffs:
        out.append("")
        out.append("Aktywne wycofania: " + ", ".join(f"{h} do {_fmt(b['until'])} ({b.get('reason')})"
                                                      for h, b in backoffs.items()))
    out.append("")
    out.append(f"Dokumenty (unikalne URL): {len(store.current)}   Wersje (linie w documents.jsonl): {store.count_versions()}")
    return "\n".join(out)


def build_ctx(cfg: Config, store: Store, sources: list[dict], max_items: int | None) -> Ctx:
    ua = cfg.user_agent()  # rzuca ConfigError bez maila kontaktowego
    masker = None
    if any(s.get("mask_pii") for s in sources):
        from .pii import PiiMasker
        masker = PiiMasker(list(cfg.settings["spacy_models"]))  # rzuca PiiUnavailable → brak startu
    return Ctx(store=store, fetcher=Fetcher(cfg.settings, store, ua), masker=masker,
               max_items_override=max_items)
