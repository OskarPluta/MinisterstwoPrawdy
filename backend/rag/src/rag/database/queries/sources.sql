-- name: upsert
-- Catalog fields are always written. Fetch status (last_success_at, last_error) is
-- written only when the caller sets has_last_success_at / has_last_error, so
-- reloading the catalog keeps the last recorded success or error.
INSERT INTO sources (source_id, publisher, publishing_domain, source_type, terms_url, license,
                     attribution, processed_notice, citation_url, refresh_policy, last_success_at,
                     last_error)
VALUES (%(source_id)s, %(publisher)s, %(publishing_domain)s, %(source_type)s, %(terms_url)s,
        %(license)s, %(attribution)s, %(processed_notice)s, %(citation_url)s, %(refresh_policy)s,
        %(last_success_at)s, %(last_error)s)
ON CONFLICT (source_id) DO UPDATE SET
    publisher         = excluded.publisher,
    publishing_domain = excluded.publishing_domain,
    source_type       = excluded.source_type,
    terms_url         = excluded.terms_url,
    license           = excluded.license,
    attribution       = excluded.attribution,
    processed_notice  = excluded.processed_notice,
    citation_url      = excluded.citation_url,
    refresh_policy    = excluded.refresh_policy,
    last_success_at   = CASE WHEN %(has_last_success_at)s THEN excluded.last_success_at
                             ELSE sources.last_success_at END,
    last_error        = CASE WHEN %(has_last_error)s THEN excluded.last_error
                             ELSE sources.last_error END;

-- name: domains
SELECT source_id, publishing_domain FROM sources;

-- name: list
-- Source catalog for the public audit page: fetch status plus the number of current records.
SELECT s.source_id, s.publisher, s.publishing_domain, s.source_type, s.citation_url,
       s.terms_url, s.license, s.attribution, s.last_success_at, s.last_error,
       COUNT(e.evidence_id) AS current_records
FROM sources s
LEFT JOIN evidence e ON e.source_id = s.source_id AND e.is_current
GROUP BY s.source_id
ORDER BY s.publisher, s.source_id;

-- name: delete
-- Procedure of removal (rag purge): run after the source's evidence is deleted.
DELETE FROM sources WHERE source_id = ANY(%(sources)s);
