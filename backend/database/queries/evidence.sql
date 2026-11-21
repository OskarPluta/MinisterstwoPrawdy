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
