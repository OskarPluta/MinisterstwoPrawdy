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
    license            TEXT,  -- licence or legal basis (docs/DANE_I_LICENCJE.md)
    attribution        TEXT,  -- source credit shown under every citation
    processed_notice   TEXT,  -- extra credit under template-rendered data (IMGW terms)
    citation_url       TEXT,
    refresh_policy     TEXT,
    last_success_at    TEXT,
    last_error         TEXT,
    -- Admin panel (admin-backend): never written by ingest, so a reload keeps them.
    -- A disabled source drops out of search; admin_* override the ingested catalog fields.
    enabled            BOOLEAN NOT NULL DEFAULT TRUE,
    admin_publisher    TEXT,
    admin_source_type  TEXT,
    admin_note         TEXT,
    admin_updated_at   TEXT
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
    -- Withdrawn by an admin: out of search, and ingest never brings the identity back.
    withdrawn_at    TEXT,
    withdrawn_note  TEXT,
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

-- Reprint detection (rag/reprints.py), derived from the passages of current articles:
-- a MinHash signature of word 5-shingles and the detected original ("source_id:upstream_id",
-- NULL for an original or a unique text). Not part of the record's content hash, so
-- detection never creates new evidence versions. `active` is cleared once a retired
-- version's cluster has been recomputed.
CREATE TABLE IF NOT EXISTS reprint_signatures (
    evidence_id BIGINT PRIMARY KEY REFERENCES evidence(evidence_id),
    shingles    INTEGER NOT NULL,
    minhash     BYTEA NOT NULL,
    reprint_of  TEXT,
    active      BOOLEAN NOT NULL DEFAULT TRUE
);
CREATE INDEX IF NOT EXISTS reprint_signatures_root ON reprint_signatures(reprint_of);

-- Check log. Kept for PRAWDOWID_CHECKS_RETENTION_DAYS (rag/verify.py), deletable by check_id.
-- share_id: random key of the public read-only page /wynik/<share_id>, set only when the person
-- shares the result; result_json is never updated, so a shared result cannot change.
CREATE TABLE IF NOT EXISTS checks (
    check_id        TEXT PRIMARY KEY,
    created_at      TEXT NOT NULL,
    input_masked    TEXT NOT NULL,
    status          TEXT NOT NULL,
    result_json     TEXT NOT NULL,
    corpus_version  INTEGER NOT NULL,
    model           TEXT,
    embedding_model TEXT,
    duration_ms     INTEGER,
    share_id        TEXT UNIQUE,
    shared_at       TEXT,
    -- Derived by code at record time (rag/outcomes.py); copies of what result_json holds,
    -- kept as columns so the admin panel can filter and sum without parsing every row.
    channel         TEXT,     -- web | discord | cli | eval
    outcome         TEXT,     -- rag.outcomes.OUTCOMES
    error_type      TEXT,     -- exception class name of the failure, if any
    llm_calls       INTEGER,
    input_tokens    INTEGER,
    output_tokens   INTEGER,
    cost            DOUBLE PRECISION,
    cost_currency   TEXT
);
CREATE INDEX IF NOT EXISTS checks_created ON checks(created_at);
CREATE INDEX IF NOT EXISTS checks_outcome ON checks(outcome, created_at);

-- Manual categorisation by an admin; the check log row itself is never edited. Deleting the
-- check (retention, /usun, purge) deletes its review too.
CREATE TABLE IF NOT EXISTS check_reviews (
    check_id    TEXT PRIMARY KEY REFERENCES checks(check_id) ON DELETE CASCADE,
    label       TEXT NOT NULL,
    note        TEXT,
    reviewed_at TEXT NOT NULL
);

-- Processing problems for the admin (rag/alerts.py). One open alert per fingerprint:
-- repeats raise `count`. Messages never contain the user's text.
CREATE TABLE IF NOT EXISTS alerts (
    alert_id        BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    created_at      TEXT NOT NULL,
    last_seen_at    TEXT NOT NULL,
    kind            TEXT NOT NULL,
    severity        TEXT NOT NULL,   -- error | warning
    fingerprint     TEXT NOT NULL,
    check_id        TEXT,            -- latest check that raised it
    message         TEXT NOT NULL,
    count           INTEGER NOT NULL DEFAULT 1,
    notified_at     TEXT,
    notified_count  INTEGER NOT NULL DEFAULT 0,
    acknowledged_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS alerts_open_fingerprint ON alerts(fingerprint) WHERE acknowledged_at IS NULL;
CREATE INDEX IF NOT EXISTS alerts_fingerprint ON alerts(fingerprint);

-- Fact-check domains whose reviews live lookup accepts (rag/live.py). Seeded once from
-- DEFAULT_TRUSTED_FACTCHECK_DOMAINS; the admin panel adds and removes rows.
CREATE TABLE IF NOT EXISTS trusted_domains (
    domain    TEXT PRIMARY KEY,
    kind      TEXT NOT NULL DEFAULT 'factcheck',
    enabled   BOOLEAN NOT NULL DEFAULT TRUE,
    note      TEXT,
    added_at  TEXT NOT NULL
);

-- Every change made in the admin panel.
CREATE TABLE IF NOT EXISTS admin_audit (
    audit_id     BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    at           TEXT NOT NULL,
    action       TEXT NOT NULL,
    target       TEXT,
    details_json TEXT NOT NULL DEFAULT '{}'
);

INSERT INTO meta (key, value) VALUES ('corpus_version', '0') ON CONFLICT (key) DO NOTHING;
