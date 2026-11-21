"""Compare BM25-only, dense-only and BM25+dense+RRF on the labelled retrieval set.

    uv run python scripts/eval_retrieval.py                    # real model (default: e5)
    uv run python scripts/eval_retrieval.py --embedder hash    # offline stub
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from rag.config import load_env
from rag.db import Connection, corpus_totals, scratch_db
from rag.embed import DEFAULT_EMBEDDER, get_embedder
from rag.evaluation import evaluate_retrieval, load_cases
from rag.ingest import ingest_corpus, load_corpus
from rag.search import Retriever

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--embedder", default=DEFAULT_EMBEDDER)
    ap.add_argument(
        "--corpus", default=str(ROOT / "backend/rag/tests/fixtures/fake_corpus.json"),
        help="corpus JSON file or crawler data directory (e.g. data/snapshot)",
    )
    ap.add_argument("--cases", default=str(ROOT / "backend/rag/tests/fixtures/retrieval_cases.yaml"))
    ap.add_argument("--k", type=int, default=8)
    ap.add_argument("--no-prefix-stem", action="store_true")
    ap.add_argument("--show-misses", action="store_true")
    ap.add_argument("--json-out")
    args = ap.parse_args()
    load_env()
    with scratch_db() as conn:  # throwaway schema in DATABASE_URL, dropped on exit
        run(conn, args)


def run(conn: Connection, args: argparse.Namespace) -> None:
    t0 = time.monotonic()
    emb = get_embedder(args.embedder)
    ingest_corpus(conn, load_corpus(args.corpus), emb)
    print(f"embedder={emb.name}  ingest={time.monotonic() - t0:.1f}s  totals={corpus_totals(conn)}")

    r = Retriever(conn, emb, prefix_stem=not args.no_prefix_stem)
    cases = load_cases(args.cases)["cases"]
    results = {}
    for mode in ("bm25", "dense", "hybrid"):
        res = evaluate_retrieval(r, cases, mode, k=args.k)
        results[mode] = res
    tags = list(results["hybrid"]["summary"])
    cols = [c for c in results["hybrid"]["summary"]["ALL"] if c != "n"]
    print(f"\n{'tag':<12}{'n':>4}  " + "  ".join(f"{m:^26}" for m in results))
    print(f"{'':<16}" + "  ".join(" ".join(f"{c:>8}" for c in cols) for _ in results))
    for t in tags:
        n = results["hybrid"]["summary"][t]["n"]
        row = "  ".join(
            " ".join(f"{results[m]['summary'][t][c]:>8.2f}" for c in cols) for m in results
        )
        print(f"{t:<12}{n:>4}  {row}")

    if args.show_misses:
        for mode, res in results.items():
            misses = [(c["id"], c["rank"], c["top"]) for c in res["cases"] if not c["hit"] or c["rank"] > 1]
            print(f"\n{mode}: not at rank 1 -> {len(misses)}")
            for m in misses:
                print("  ", m)
    if args.json_out:
        Path(args.json_out).write_text(json.dumps({"embedder": emb.name, **results}, indent=2))


if __name__ == "__main__":
    main()
