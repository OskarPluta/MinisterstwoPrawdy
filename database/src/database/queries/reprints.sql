-- name: lock
-- Serializes reprint detection (CLI ingest and live lookups may run at once).
SELECT pg_advisory_xact_lock(hashtext('prawdowid.reprints'));

-- name: unsigned
-- Current articles that take part in detection but have no signature yet (new versions,
-- or records ingested before schema v2). Explicit `syndicated_from` is kept as given.
SELECT e.evidence_id
FROM evidence e
JOIN sources s USING (source_id)
LEFT JOIN reprint_signatures r ON r.evidence_id = e.evidence_id
WHERE e.is_current AND e.kind = ANY(%(kinds)s) AND s.source_type <> 'structured'
  AND e.syndicated_from IS NULL AND r.evidence_id IS NULL
ORDER BY e.evidence_id;

-- name: texts
SELECT evidence_id, search_text FROM passages
WHERE evidence_id = ANY(%(ids)s)
ORDER BY evidence_id, ord;

-- name: insert_signature
INSERT INTO reprint_signatures (evidence_id, shingles, minhash)
VALUES (%(evidence_id)s, %(shingles)s, %(minhash)s)
ON CONFLICT (evidence_id) DO NOTHING;

-- name: current
SELECT r.evidence_id, r.shingles, r.minhash, r.reprint_of, e.source_id, e.upstream_id,
       e.published_at, e.fetched_at
FROM reprint_signatures r
JOIN evidence e ON e.evidence_id = r.evidence_id
WHERE e.is_current AND e.syndicated_from IS NULL
ORDER BY r.evidence_id;

-- name: retired
-- Signed versions that left search and whose cluster has not been recomputed yet.
SELECT r.evidence_id, e.source_id, e.upstream_id, r.reprint_of
FROM reprint_signatures r
JOIN evidence e ON e.evidence_id = r.evidence_id
WHERE r.active AND NOT e.is_current;

-- name: deactivate
UPDATE reprint_signatures SET active = FALSE WHERE evidence_id = ANY(%(ids)s);

-- name: set_reprint_of
UPDATE reprint_signatures SET reprint_of = %(reprint_of)s WHERE evidence_id = %(evidence_id)s;

-- name: delete_by_evidence
-- Procedure of removal (rag purge): run before the evidence rows are deleted.
DELETE FROM reprint_signatures WHERE evidence_id = ANY(%(ids)s);
