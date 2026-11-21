# Prawdowid: RAG prototype

First version of the verification core from [`docs/plan.md`](docs/plan.md): PostgreSQL store with full-text search, BM25-style + multilingual-e5 + RRF retrieval, an LLM verifier with code-side citation checks, and Polish answers. Everything currently runs on **fake test data** (`fixtures/`, all URLs on the reserved `.test` TLD).

## Setup
```bash
cp .env.example .env    # then set POSTGRES_PASSWORD / DATABASE_URL and API keys
docker compose up -d db # PostgreSQL 16 on localhost:5432 (or point DATABASE_URL at any Postgres >= 14)
uv sync                 # core + tests (hashing embedder, no model download)
uv sync --extra e5      # + sentence-transformers / CPU torch for multilingual-e5-small
```
Configuration is read from the environment, filled from `.env` (git-ignored; template in `.env.example`); variables already set in the shell win. The schema is created on first connect.

LLM backend: OpenAI by default (`OPENAI_API_KEY`, `OPENAI_MODEL`, default `gpt-5.5`; optional `OPENAI_REASONING_EFFORT`). Use `--provider anthropic` (or `PRAWDOWID_PROVIDER=anthropic`) with `ANTHROPIC_API_KEY` for Claude. Cost is reported for models with known prices; for others set `PRAWDOWID_PRICE_IN` / `PRAWDOWID_PRICE_OUT` (USD per 1M tokens). Each LLM request times out after `PRAWDOWID_LLM_TIMEOUT` seconds (default 60, one retry). Inputs over 10,000 characters are refused and at most 5 claims are checked per message.

## Use
```bash
uv run pytest                                                     # needs Postgres (TEST_DATABASE_URL or DATABASE_URL)
uv run prawdowid ingest fixtures/fake_corpus.json --embedder e5
uv run prawdowid search "ostrzeżenie przed wiatrem na Mazowszu" --embedder e5
uv run prawdowid verify "Czy IMGW wydał dziś ostrzeżenie stopnia 2 dla całego Mazowsza?" \
    --embedder e5 --now 2026-11-21T12:00
uv run python scripts/eval_retrieval.py --embedder e5 --show-misses   # BM25 vs dense vs hybrid
uv run python scripts/eval_verify.py --embedder e5                    # live, ~30 LLM calls (costs money)
```
Use the same `--embedder` for `ingest`, `search` and `verify`, or run `prawdowid reembed --embedder e5`. Every command takes `--database-url` to override `DATABASE_URL`.

Tests and eval scripts each work in a throwaway Postgres schema (`scratch_<id>`) that is dropped afterwards, so they never touch the main tables. Without a reachable database the DB-backed tests are skipped.

## Layout
```
backend/            Python package (CLI entry point: `prawdowid`)
  database/         all SQL: schema.sql + queries/<group>.sql (named statements)
docs/               task description, rules, plan, example analysis, extra sources
fixtures/           fake corpus and evaluation cases
scripts/            retrieval / verification evaluations
tests/              pytest suite
```

| Module | Role |
|---|---|
| `backend/config.py` | `.env` loading, `DATABASE_URL` |
| `backend/database/` | SQL files and the loader: `query("evidence.insert")` returns the `-- name: insert` statement from `queries/evidence.sql` |
| `backend/db.py` | connection + schema setup, versioned upsert in one transaction, `scratch_db()` for tests |
| `backend/timeutil.py` | UTC storage / Europe/Warsaw display, tolerant ISO 8601 parsing |
| `backend/chunking.py` | section/paragraph chunks (≤300 words), fixed Polish templates for structured records |
| `backend/embed.py` | `E5Embedder` (`query:`/`passage:` prefixes), `HashEmbedder` test stub |
| `backend/search.py` | `bm25` (Postgres `tsvector`, weighted title/heading/body), `dense`, `rrf` (k=60), `hybrid` with filters and per-source diversity |
| `backend/llm.py` | structured outputs (OpenAI / Anthropic): claim extraction, per-claim verdict |
| `backend/verify.py` | pipeline, citation validation, expiry/staleness notes, Markdown rendering, check log |

### SQL conventions
- No SQL in Python code: add a statement under a `-- name: <name>` header in `backend/database/queries/<group>.sql` and call it with `conn.execute(query("<group>.<name>"), params)`.
- Values go in only as psycopg placeholders (`%(name)s`), never via f-strings or `%` formatting. The only composed SQL is the schema name in `meta.create_schema` / `meta.drop_schema`, which goes through `psycopg.sql.Identifier`.
- Schema changes go in `schema.sql` together with a `SCHEMA_VERSION` bump and an upgrade step in `backend/db.py`.

## Corpus format
`{"sources": [...], "evidence": [...]}`. See `fixtures/fake_corpus.json`. Identity is `(source_id, upstream_id)`. A record with the same identity and different content becomes a new version, and the old one leaves the search index. Articles carry `sections`. Structured kinds (`warning_meteo`, `warning_hydro`, `air_reading`, `road_restriction`) carry typed `fields`. Real importers (HTML, RSS, IMGW) should emit this same shape. `citation_url` must be `https` on the source's `publishing_domain` or a subdomain; records that fail this or lack required fields are skipped with a warning and counted as `invalid`.
