-- name: upsert
-- Catalog fields are always written. Fetch status (last_success_at, last_error) is
-- written only when the caller sets has_last_success_at / has_last_error, so
-- reloading the catalog keeps the last recorded success or error.
INSERT INTO sources (source_id, publisher, publishing_domain, source_type, terms_url,
                     citation_url, refresh_policy, last_success_at, last_error)
VALUES (%(source_id)s, %(publisher)s, %(publishing_domain)s, %(source_type)s, %(terms_url)s,
        %(citation_url)s, %(refresh_policy)s, %(last_success_at)s, %(last_error)s)
ON CONFLICT (source_id) DO UPDATE SET
    publisher         = excluded.publisher,
    publishing_domain = excluded.publishing_domain,
    source_type       = excluded.source_type,
    terms_url         = excluded.terms_url,
    citation_url      = excluded.citation_url,
    refresh_policy    = excluded.refresh_policy,
    last_success_at   = CASE WHEN %(has_last_success_at)s THEN excluded.last_success_at
                             ELSE sources.last_success_at END,
    last_error        = CASE WHEN %(has_last_error)s THEN excluded.last_error
                             ELSE sources.last_error END;

-- name: domains
SELECT source_id, publishing_domain FROM sources;
