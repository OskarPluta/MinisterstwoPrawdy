-- name: create_schema
-- {schema} is filled with psycopg.sql.Identifier (identifiers cannot be bound parameters).
CREATE SCHEMA IF NOT EXISTS {schema};

-- name: drop_schema
DROP SCHEMA IF EXISTS {schema} CASCADE;

-- name: set_search_path
SELECT set_config('search_path', %(schema)s, false);

-- name: has_meta
SELECT to_regclass('meta') IS NOT NULL AS present;

-- name: schema_lock
-- Serializes concurrent schema setup (held until the transaction ends).
SELECT pg_advisory_xact_lock(hashtext('prawdowid.schema'));

-- name: get_schema_version
SELECT value FROM meta WHERE key = 'schema_version';

-- name: set_schema_version
INSERT INTO meta (key, value) VALUES ('schema_version', %(version)s)
ON CONFLICT (key) DO UPDATE SET value = excluded.value;

-- name: get_corpus_version
SELECT value::INTEGER AS value FROM meta WHERE key = 'corpus_version';

-- name: bump_corpus_version
UPDATE meta SET value = (value::INTEGER + 1)::TEXT WHERE key = 'corpus_version';

-- name: corpus_totals
-- Counts before chunking (documents/records) plus passages.
SELECT
    (SELECT COUNT(DISTINCT (source_id, upstream_id)) FROM evidence)  AS distinct_records,
    (SELECT COUNT(*) FROM evidence WHERE is_current)                  AS current_records,
    (SELECT COUNT(*) FROM evidence)                                   AS evidence_versions,
    (SELECT COUNT(DISTINCT publishing_domain) FROM sources)           AS publishing_domains,
    (SELECT COUNT(DISTINCT source_type) FROM sources)                 AS source_types,
    (SELECT COUNT(*) FROM passages WHERE fts IS NOT NULL)             AS passages_searchable;
