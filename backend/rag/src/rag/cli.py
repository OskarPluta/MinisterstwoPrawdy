"""rag ingest | reembed | stats | search | verify"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from pathlib import Path

import typer

from . import ingest as ingest_mod
from .config import load_env
from .db import Connection, checks_stats, corpus_totals
from .db import connect as _db_connect
from .embed import DEFAULT_EMBEDDER, get_embedder
from .search import Filters, Retriever
from .timeutil import WARSAW, parse_ts

app = typer.Typer(add_completion=False, no_args_is_help=True)
DB_HELP = "PostgreSQL URL (default: DATABASE_URL from the environment or .env)"
EMBEDDER_HELP = "e5 | hash (tests/offline only) | <sentence-transformers model>"


@app.callback()
def _main() -> None:
    load_env()


def connect(url: str | None) -> Connection:
    import psycopg

    try:
        return _db_connect(url)
    except psycopg.OperationalError as e:
        raise SystemExit(f"cannot connect to PostgreSQL: {str(e).strip().splitlines()[0]}") from None


def _now(now: str | None) -> datetime:
    if not now:
        return datetime.now(WARSAW)
    dt = parse_ts(now, naive_tz=WARSAW)
    if dt is None:
        raise typer.BadParameter(f"not an ISO 8601 time: {now!r}", param_hint="--now")
    return dt


@app.command()
def ingest(
    corpus: Path = typer.Argument(Path("data/snapshot"), help="Crawler data directory or corpus JSON file"),
    db: str | None = typer.Option(None, "--database-url", help=DB_HELP),
    embedder: str = typer.Option(DEFAULT_EMBEDDER, help=EMBEDDER_HELP),
):
    """Load crawler output (documents.jsonl + crawl_log.jsonl) or a corpus JSON file."""
    conn = connect(db)
    data = ingest_mod.load_corpus(corpus)
    evidence = data.get("evidence", [])
    kinds = dict(Counter(e.get("kind") for e in evidence))
    typer.echo(f"loaded {corpus}: {len(data.get('sources', []))} sources, {len(evidence)} records {kinds}")
    if data.get("_crawl_without_documents"):
        typer.echo(f"crawled but no documents: {data['_crawl_without_documents']}")
    stats = ingest_mod.ingest_corpus(conn, data, get_embedder(embedder))
    typer.echo(f"records: {dict(stats)}")
    typer.echo(f"totals:  {corpus_totals(conn)}")


@app.command()
def reembed(
    db: str | None = typer.Option(None, "--database-url", help=DB_HELP),
    embedder: str = typer.Option(DEFAULT_EMBEDDER, help=EMBEDDER_HELP),
):
    """Recompute passage vectors with another embedding model."""
    conn = connect(db)
    n = ingest_mod.reembed(conn, get_embedder(embedder))
    typer.echo(f"re-embedded {n} passages")


@app.command()
def stats(db: str | None = typer.Option(None, "--database-url", help=DB_HELP)):
    """Corpus totals (records are counted before chunking) and check observability stats."""
    c = connect(db)
    typer.echo(json.dumps({**corpus_totals(c), "checks": checks_stats(c)}, indent=2))


@app.command()
def search(
    query: str,
    db: str | None = typer.Option(None, "--database-url", help=DB_HELP),
    embedder: str = typer.Option(DEFAULT_EMBEDDER, help=EMBEDDER_HELP),
    mode: str = typer.Option("hybrid", help="hybrid | bm25 | dense"),
    k: int = 8,
    area: str | None = None,
):
    """Show the fused shortlist for a query."""
    conn = connect(db)
    r = Retriever(conn, get_embedder(embedder) if mode != "bm25" else None)
    hits = r.hybrid(query, filters=Filters(area=area) if area else None, shortlist=k, mode=mode)
    for i, h in enumerate(hits, 1):
        m = h.meta
        ranks = " ".join(f"{k}={v}" for k, v in sorted(h.ranks.items()))
        typer.echo(f"{i:>2}. P{h.passage_id} [{m['upstream_id']} v{m['version']}] rrf={h.score:.4f} {ranks}")
        typer.echo(f"    {m['title']}")
        typer.echo(f"    {m['search_text'][:160].replace(chr(10), ' ')}…")


@app.command()
def verify(
    text: str,
    db: str | None = typer.Option(None, "--database-url", help=DB_HELP),
    embedder: str = typer.Option(DEFAULT_EMBEDDER, help=EMBEDDER_HELP),
    now: str | None = typer.Option(None, help="Request time, ISO 8601 (default: now, Europe/Warsaw)"),
    as_json: bool = typer.Option(False, "--json"),
    provider: str | None = typer.Option(None, help="openai (default) | anthropic"),
    model: str | None = typer.Option(None, help="Model id (default: OPENAI_MODEL or gpt-5.5)"),
):
    """Verify a claim end to end with an LLM (needs OPENAI_API_KEY or ANTHROPIC_API_KEY)."""
    from .llm import make_llm
    from .verify import Verifier, render_markdown

    conn = connect(db)
    llm = make_llm(provider, model)
    v = Verifier(Retriever(conn, get_embedder(embedder)), llm)
    res = v.verify(text, _now(now))
    typer.echo(json.dumps(res.to_dict(), ensure_ascii=False, indent=2) if as_json else render_markdown(res))
    if res.error:
        typer.echo(f"\n[internal error] {res.error}", err=True)
    cost = llm.usage.cost_usd(llm.model)
    typer.echo(
        f"\n[{res.status}, {llm.model}, {res.duration_ms} ms, {llm.usage.calls} calls, "
        f"{llm.usage.input_tokens}+{llm.usage.output_tokens} tok, "
        f"{'cost n/a' if cost is None else f'${cost:.4f}'}]",
        err=True,
    )


if __name__ == "__main__":
    app()
