#!/usr/bin/env python3
"""FaktBot – crawler oficjalnych komunikatów.

    uv run crawler run                      # pętla ciągła wg interval_minutes
    uv run crawler once [--source NAZWA]    # jeden przebieg (wszystkie lub wybrane źródła)
    uv run crawler status                   # tabela stanu źródeł
    uv run crawler forget                   # wycina z documents.jsonl treści z removed.yaml
"""
from __future__ import annotations

import argparse
import fcntl
import logging
import signal
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore", message=".*SOCKS support.*")

from crawler.config import DEFAULT_CONFIG, ConfigError, load_config
from crawler.runner import STOP, build_ctx, run_loop, run_once, status_report
from crawler.storage import Store


def setup_logging(data_dir: Path, verbose: bool) -> None:
    """Log na stderr i do data_dir/crawler.log."""
    data_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S")
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    h = logging.StreamHandler(sys.stderr)
    h.setFormatter(fmt)
    root.addHandler(h)
    fh = logging.FileHandler(data_dir / "crawler.log", encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)
    for noisy in ("httpx", "httpcore", "trafilatura", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def install_signal_handlers() -> None:
    """Pierwszy SIGINT/SIGTERM: dokończ bieżące źródło; drugi: przerwij od razu."""
    def handler(signum, frame):
        if STOP.is_set():
            logging.getLogger().warning("Drugi sygnał – przerywam natychmiast")
            raise KeyboardInterrupt
        logging.getLogger().warning("Otrzymano sygnał %s – kończę po bieżącym źródle (ponów, by przerwać od razu)",
                                    signal.Signals(signum).name)
        STOP.set()

    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGTERM, handler)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="FaktBot – crawler oficjalnych komunikatów")
    ap.add_argument("--config", default=str(DEFAULT_CONFIG))
    ap.add_argument("--data-dir", default=None, help="nadpisuje settings.data_dir")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_run = sub.add_parser("run", help="pętla ciągła")
    p_once = sub.add_parser("once", help="jeden przebieg")
    p_once.add_argument("--source", action="append", help="nazwa źródła (można powtórzyć)")
    for p in (p_run, p_once):
        p.add_argument("--max-items", type=int, default=None, help="tymczasowo nadpisuje max_items_per_run")
    sub.add_parser("status", help="tabela stanu źródeł")
    sub.add_parser("forget", help="usuwa z documents.jsonl treści z removed.yaml (procedura usunięcia)")
    args = ap.parse_args(argv)

    try:
        cfg = load_config(args.config, args.data_dir)
    except ConfigError as e:
        print(f"Błąd konfiguracji: {e}", file=sys.stderr)
        return 2

    if args.cmd == "status":
        print(status_report(cfg, Store(cfg.data_dir, readonly=True)))
        return 0

    setup_logging(cfg.data_dir, args.verbose)
    log = logging.getLogger("crawler")

    sources = [s for s in cfg.sources if s.get("enabled", True)]
    if args.cmd == "once" and args.source:
        try:
            sources = [cfg.source(n) for n in args.source]
            for s in sources:
                if not s.get("enabled", True):
                    log.warning("Źródło %s jest wyłączone w sources.yaml – uruchamiam, bo podano je w --source", s["name"])
        except ConfigError as e:
            log.error("%s", e)
            return 2

    lock_file = (cfg.data_dir / ".lock").open("w")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log.error("Inny proces crawlera już działa na %s", cfg.data_dir)
        return 3

    if args.cmd == "forget":
        n = Store(cfg.data_dir).forget(cfg.removals)
        log.info("removed.yaml: usunięto %d wersji (%d adresów) z %s", n["lines"], n["urls"],
                 cfg.data_dir / "documents.jsonl")
        print(f"Usunięto {n['lines']} wersji dokumentów ({n['urls']} adresów). "
              "Teraz `uv run rag purge`, a dla data/snapshot także commit.")
        return 0

    store = Store(cfg.data_dir, removals=cfg.removals)
    try:
        ctx = build_ctx(cfg, store, args.max_items)
    except ConfigError as e:
        log.error("Odmowa startu: %s", e)
        return 2

    log.info("User-Agent: %s", ctx.fetcher.ua)
    install_signal_handlers()
    try:
        if args.cmd == "run":
            run_loop(ctx, sources)
        else:
            run_once(ctx, sources)
    except KeyboardInterrupt:
        log.warning("Przerwano.")
    finally:
        store.save_state()
        ctx.fetcher.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
