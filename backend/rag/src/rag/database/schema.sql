-- Prawdowid schema (PostgreSQL >= 14). Idempotent: safe to run on every connect.
-- Bump SCHEMA_VERSION in rag/db.py together with any change here, and add an
-- upgrade step there for databases created by an older version.
--
-- Timestamps are stored as ISO 8601 text in UTC (see rag/timeutil.py) and
-- compared in Python; JSON payloads are stored as text for the same reason.

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sources (
    source_id          TEXT PRIMARY KEY,
    publisher          TEXT NOT NULL,
    publishing_domain  TEXT NOT NULL,
    source_type        TEXT NOT NULL,
    terms_url          TEXT,
    citation_url       TEXT,
    refresh_policy     TEXT,
    last_success_at    TEXT,
    last_error         TEXT
);

CREATE TABLE IF NOT EXISTS evidence (
    evidence_id     BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_id       TEXT NOT NULL REFERENCES sources(source_id),
    upstream_id     TEXT NOT NULL,
    version         INTEGER NOT NULL,
    is_current      BOOLEAN NOT NULL DEFAULT TRUE,
    superseded_by   BIGINT REFERENCES evidence(evidence_id),
    content_hash    TEXT NOT NULL,
    kind            TEXT NOT NULL,
    title           TEXT NOT NULL,
    citation_url    TEXT NOT NULL,
    published_at    TEXT,
    fetched_at      TEXT NOT NULL,
    observed_at     TEXT,
    valid_from      TEXT,
    valid_to        TEXT,
    areas_json      TEXT NOT NULL DEFAULT '[]',
    status          TEXT,
    syndicated_from TEXT,
    fields_json     TEXT NOT NULL DEFAULT '{}',
    UNIQUE (source_id, upstream_id, version)
);
CREATE INDEX IF NOT EXISTS evidence_identity ON evidence(source_id, upstream_id, is_current);

-- `fts` is the full-text index entry: title (weight A), heading (B) and body (C),
-- folded to lowercase ASCII by the application. NULL means "not searchable"
-- (passages of superseded versions are kept for citation traceability).
CREATE TABLE IF NOT EXISTS passages (
    passage_id      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    evidence_id     BIGINT NOT NULL REFERENCES evidence(evidence_id),
    ord             INTEGER NOT NULL,
    heading         TEXT,
    page            INTEGER,
    excerpt         TEXT,
    search_text     TEXT NOT NULL,
    is_verbatim     BOOLEAN NOT NULL DEFAULT TRUE,
    embedding       BYTEA,
    embedding_model TEXT,
    fts             TSVECTOR
);
CREATE INDEX IF NOT EXISTS passages_evidence ON passages(evidence_id);
CREATE INDEX IF NOT EXISTS passages_fts ON passages USING GIN (fts);

CREATE TABLE IF NOT EXISTS checks (
    check_id        TEXT PRIMARY KEY,
    created_at      TEXT NOT NULL,
    input_masked    TEXT NOT NULL,
    status          TEXT NOT NULL,
    result_json     TEXT NOT NULL,
    corpus_version  INTEGER NOT NULL,
    model           TEXT,
    embedding_model TEXT,
    duration_ms     INTEGER
);

INSERT INTO meta (key, value) VALUES ('corpus_version', '0') ON CONFLICT (key) DO NOTHING;
