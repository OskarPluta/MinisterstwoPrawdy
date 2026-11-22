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
-- Admin overrides (admin_publisher, admin_source_type) replace the ingested values.
SELECT s.source_id, COALESCE(s.admin_publisher, s.publisher) AS publisher, s.publishing_domain,
       COALESCE(s.admin_source_type, s.source_type) AS source_type, s.citation_url,
       s.terms_url, s.license, s.attribution, s.last_success_at, s.last_error, s.enabled,
       COUNT(e.evidence_id) AS current_records
FROM sources s
LEFT JOIN evidence e ON e.source_id = s.source_id AND e.is_current
GROUP BY s.source_id
ORDER BY s.publisher, s.source_id;

-- name: delete
-- Procedure of removal (rag purge): run after the source's evidence is deleted.
DELETE FROM sources WHERE source_id = ANY(%(sources)s);

-- name: admin_list
-- Admin panel: ingested values and overrides side by side, plus record counts.
SELECT s.source_id, s.publisher, s.admin_publisher, s.publishing_domain, s.source_type,
       s.admin_source_type, s.citation_url, s.terms_url, s.refresh_policy, s.last_success_at,
       s.last_error, s.enabled, s.admin_note, s.admin_updated_at,
       COUNT(e.evidence_id) FILTER (WHERE e.is_current) AS current_records,
       COUNT(DISTINCT e.upstream_id) FILTER (WHERE e.withdrawn_at IS NOT NULL) AS withdrawn_records
FROM sources s
LEFT JOIN evidence e ON e.source_id = s.source_id
GROUP BY s.source_id
ORDER BY COALESCE(s.admin_publisher, s.publisher), s.source_id;

-- name: get
SELECT * FROM sources WHERE source_id = %(source_id)s;

-- name: admin_update
UPDATE sources SET enabled = %(enabled)s, admin_publisher = %(admin_publisher)s,
                   admin_source_type = %(admin_source_type)s, admin_note = %(admin_note)s,
                   admin_updated_at = %(at)s
WHERE source_id = %(source_id)s;

-- name: status_rows
-- Alert rules (admin-backend notifier): fetch status of every source.
SELECT source_id, COALESCE(admin_publisher, publisher) AS publisher, enabled,
       last_success_at, last_error
FROM sources;
