"""`intake read <file|url>...`: show what intake reads from material, without a verdict.

Images use the configured LLM (a paid call) unless --ocr tesseract|none is given.
"""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import asdict
from pathlib import Path

from rag.attachments import prepare, summary
from rag.config import load_env

from .core import Upload, make_intake


def main() -> None:
    ap = argparse.ArgumentParser(prog="intake", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("read", help="read files or links and print what was extracted")
    r.add_argument("items", nargs="+", help="file paths or http(s) addresses")
    r.add_argument("--ocr", choices=["llm", "tesseract", "none"], default=None)
    r.add_argument("--json", action="store_true", help="print JSON instead of text")
    args = ap.parse_args()

    load_env()
    logging.basicConfig(level=logging.WARNING)
    intake = make_intake()
    intake.ocr_mode = args.ocr
    transcriber = None
    if args.ocr in (None, "llm"):
        from rag.llm import make_llm

        transcriber = make_llm()

    atts = []
    for item in args.items:
        if item.startswith(("http://", "https://", "www.")):
            atts += intake.collect(item, transcriber=transcriber)
        else:
            p = Path(item)
            atts.append(intake.read_file(Upload(p.name, p.read_bytes()), transcriber))
    for s in map(summary, prepare(atts)):
        if args.json:
            print(json.dumps(asdict(s), ensure_ascii=False, indent=2))
            continue
        print(f"== {s.id} {s.kind_pl}: {s.label}  [{s.method or 'nie odczytano'}]")
        if s.date_label:
            print(f"   data: {s.date_label}")
        for n in s.notes:
            print(f"   ℹ {n}")
        for n in s.limitations:
            print(f"   ⚠ {n}")
        if s.text:
            print("   ---\n   " + s.text.replace("\n", "\n   "))
        print()
