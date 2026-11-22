-- name: seed
INSERT INTO trusted_domains (domain, kind, enabled, note, added_at)
VALUES (%(domain)s, 'factcheck', TRUE, NULL, %(at)s)
ON CONFLICT (domain) DO NOTHING;

-- name: enabled
SELECT domain FROM trusted_domains WHERE enabled AND kind = 'factcheck' ORDER BY domain;

-- name: list
SELECT domain, kind, enabled, note, added_at FROM trusted_domains ORDER BY domain;

-- name: upsert
INSERT INTO trusted_domains (domain, kind, enabled, note, added_at)
VALUES (%(domain)s, 'factcheck', %(enabled)s, %(note)s, %(at)s)
ON CONFLICT (domain) DO UPDATE SET enabled = excluded.enabled, note = excluded.note;

-- name: delete
DELETE FROM trusted_domains WHERE domain = %(domain)s RETURNING domain;
