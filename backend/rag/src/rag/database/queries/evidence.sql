-- name: find_by_hash
SELECT evidence_id, version, is_current FROM evidence
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
