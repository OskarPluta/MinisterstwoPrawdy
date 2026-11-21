"""Live end-to-end run of the verifier on backend/rag/tests/fixtures/verify_cases.yaml.

Calls the LLM (2 calls per case, ~15 cases), so it costs money and needs OPENAI_API_KEY
(default provider) or ANTHROPIC_API_KEY with --provider anthropic. Prints per-case outcome,
accuracy, citation checks, latency and cost.

    uv run python scripts/eval_verify.py                 # default embedder: e5
    uv run python scripts/eval_verify.py --only v01,v07
"""

from __future__ import annotations

import argparse
import json
import statistics
from datetime import datetime
from pathlib import Path

from rag.config import load_env
from rag.db import Connection, scratch_db
from rag.embed import DEFAULT_EMBEDDER, get_embedder
from rag.evaluation import load_cases
from rag.ingest import ingest_corpus, load_corpus
from rag.llm import make_llm
from rag.search import Retriever, passage_meta
from rag.verify import Verifier, render_markdown

ROOT = Path(__file__).resolve().parent.parent


def expected_verdicts(case: dict) -> list[str]:
    """YAML reads bare true/false as booleans; map them back to verdict codes."""
    raw = case.get("expect_any") or [case["expect"]]
    return [str(v).lower() if isinstance(v, bool) else v for v in raw]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--embedder", default=DEFAULT_EMBEDDER)
    ap.add_argument("--only", help="comma-separated case ids")
    ap.add_argument("--provider", default=None, help="openai (default) | anthropic")
    ap.add_argument("--model", default=None)
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--json-out")
    args = ap.parse_args()
    load_env()
    with scratch_db() as conn:  # throwaway schema in DATABASE_URL, dropped on exit
        run(conn, args)


def run(conn: Connection, args: argparse.Namespace) -> None:
    emb = get_embedder(args.embedder)
    ingest_corpus(conn, load_corpus(ROOT / "backend/rag/tests/fixtures/fake_corpus.json"), emb)
    spec = load_cases(ROOT / "backend/rag/tests/fixtures/verify_cases.yaml")
    now = datetime.fromisoformat(spec["now"])
    cases = spec["cases"]
    if args.only:
        wanted = set(args.only.split(","))
        cases = [c for c in cases if c["id"] in wanted]

    llm = make_llm(args.provider, args.model)
    print(f"provider model: {llm.model}")
    v = Verifier(Retriever(conn, emb), llm)
    rows, latencies = [], []
    for i, c in enumerate(cases):
        res = v.verify(c["input"], now)
        if res.status == "error":
            print(f"XX {c['id']:<4} error: {res.error}")
            if i == 0:
                raise SystemExit("first case failed with an error; stopping (fix the error above)")
        latencies.append(res.duration_ms)
        expected = expected_verdicts(c)
        if res.status != "ok":
            got, cited_keys, notes = res.status, set(), []
        else:
            cr = res.claims[0]  # cases are single-claim
            got = cr.verdict
            ids = [int(cit.passage_id[1:]) for cit in cr.citations]
            meta = passage_meta(conn, ids)
            cited_keys = {f"{m['source_id']}:{m['upstream_id']}" for m in meta.values()}
            notes = [n for cl in res.claims for n in cl.notes]
        verdict_ok = got in expected
        must = set(c.get("must_cite") or [])
        cite_ok = (not must) or bool(must & cited_keys)
        note_ok = ("expect_note" not in c) or any(c["expect_note"] in n for n in notes)
        rows.append({
            "id": c["id"], "tags": c["tags"], "expected": expected, "got": got,
            "verdict_ok": verdict_ok, "cite_ok": cite_ok, "note_ok": note_ok,
            "ms": res.duration_ms, "invented": [i for cl in res.claims for i in cl.invented_ids],
        })
        flag = "OK " if verdict_ok and cite_ok and note_ok else "XX "
        print(f"{flag}{c['id']:<4} expected={'/'.join(expected):<24} got={got:<20} "
              f"cite={'ok' if cite_ok else 'MISS'} {res.duration_ms} ms")
        if args.verbose or not (verdict_ok and cite_ok):
            print("    " + render_markdown(res).replace("\n", "\n    "))

    n = len(rows)
    print(f"\nverdict accuracy: {sum(r['verdict_ok'] for r in rows)}/{n}")
    print(f"required citation present: {sum(r['cite_ok'] for r in rows)}/{n}")
    print(f"invented citation ids caught: {sum(len(r['invented']) for r in rows)}")
    print(f"latency median: {statistics.median(latencies) / 1000:.1f}s  max: {max(latencies) / 1000:.1f}s")
    cost = llm.usage.cost_usd(llm.model)
    cost_s = (
        "cost n/a (set PRAWDOWID_PRICE_IN/OUT, USD per 1M tokens)" if cost is None
        else f"cost=${cost:.3f} (≈ ${cost / max(n, 1) * 1000:.0f} per 1000 checks)"
    )
    print(f"model={llm.model} calls={llm.usage.calls} tokens={llm.usage.input_tokens}+"
          f"{llm.usage.output_tokens} {cost_s}")
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(rows, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
