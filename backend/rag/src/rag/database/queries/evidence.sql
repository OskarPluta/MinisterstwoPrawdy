-- name: find_by_hash
SELECT evidence_id, version, is_current, withdrawn_at FROM evidence
WHERE source_id = %(source_id)s AND upstream_id = %(upstream_id)s AND content_hash = %(content_hash)s;

-- name: find_current
SELECT evidence_id, version FROM evidence
WHERE source_id = %(source_id)s AND upstream_id = %(upstream_id)s AND is_current;

-- name: insert
INSERT INTO evidence (source_id, upstream_id, version, is_current, content_hash, kind, title,
                      citation_url, published_at, fetched_at, observed_at, valid_from, valid_to,
                      areas_json, status, syndicated_from, fields_json)
VALUES (%(source_id)s, %(upstream_id)s, %(version)s, TRUE, %(content_hash)s, %(kind)s, %(title)s,
        %(citation_url)s, %(published_at)s, %(fetched_at)s, %(observed_at)s, %(valid_from)s,
        %(valid_to)s, %(areas_json)s, %(status)s, %(syndicated_from)s, %(fields_json)s)
RETURNING evidence_id;

-- name: retire
UPDATE evidence SET is_current = FALSE, superseded_by = %(new_id)s WHERE evidence_id = %(old_id)s;

-- name: current_by_urls
-- Live lookup (rag/live.py): current records that already cite one of these URLs.
SELECT evidence_id, source_id, upstream_id, citation_url FROM evidence
WHERE is_current AND citation_url = ANY(%(urls)s);

-- name: purge_targets
-- Every version of every record that cites one of the URLs (or has it as upstream id) or belongs
-- to one of the sources. Whole identities go, so no superseded_by points at a deleted row.
SELECT evidence_id, citation_url FROM evidence
WHERE (source_id, upstream_id) IN (
    SELECT source_id, upstream_id FROM evidence
    WHERE citation_url = ANY(%(urls)s) OR upstream_id = ANY(%(urls)s) OR source_id = ANY(%(sources)s));

-- name: delete
DELETE FROM evidence WHERE evidence_id = ANY(%(ids)s);

-- name: is_withdrawn
-- An identity an admin withdrew; ingest must not bring it back (rag/db.py:upsert_evidence).
SELECT evidence_id, version FROM evidence
WHERE source_id = %(source_id)s AND upstream_id = %(upstream_id)s AND withdrawn_at IS NOT NULL
ORDER BY version DESC
LIMIT 1;

-- name: get
SELECT evidence_id, source_id, upstream_id, version, is_current, kind, title, citation_url,
       published_at, fetched_at, withdrawn_at, withdrawn_note
FROM evidence WHERE evidence_id = %(evidence_id)s;

-- name: latest_version
SELECT evidence_id FROM evidence
WHERE source_id = %(source_id)s AND upstream_id = %(upstream_id)s
ORDER BY version DESC
LIMIT 1;

-- name: withdraw
-- Every version of the identity is marked, so no older version can be made current again.
UPDATE evidence SET is_current = FALSE, withdrawn_at = %(at)s, withdrawn_note = %(note)s
WHERE source_id = %(source_id)s AND upstream_id = %(upstream_id)s
RETURNING evidence_id;

-- name: unwithdraw
UPDATE evidence SET withdrawn_at = NULL, withdrawn_note = NULL
WHERE source_id = %(source_id)s AND upstream_id = %(upstream_id)s;

-- name: make_current
UPDATE evidence SET is_current = TRUE WHERE evidence_id = %(evidence_id)s;

-- name: by_source
-- Admin: the latest version of each record of a source, current and withdrawn ones, newest first.
SELECT DISTINCT ON (e.upstream_id)
       e.evidence_id, e.upstream_id, e.version, e.is_current, e.kind, e.title, e.citation_url,
       e.published_at, e.fetched_at, e.withdrawn_at, e.withdrawn_note
FROM evidence e
WHERE e.source_id = %(source_id)s
ORDER BY e.upstream_id, e.version DESC;
