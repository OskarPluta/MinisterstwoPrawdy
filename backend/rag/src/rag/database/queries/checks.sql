-- name: insert
INSERT INTO checks (check_id, created_at, input_masked, status, result_json, corpus_version,
                    model, embedding_model, duration_ms)
VALUES (%(check_id)s, %(created_at)s, %(input_masked)s, %(status)s, %(result_json)s,
        %(corpus_version)s, %(model)s, %(embedding_model)s, %(duration_ms)s);

-- name: created
-- Retention (rag/verify.py prune_checks): times are compared in Python (UTC ISO text).
SELECT check_id, created_at FROM checks;

-- name: delete
DELETE FROM checks WHERE check_id = ANY(%(ids)s);

-- name: delete_citing
-- Procedure of removal: check logs that quote or link a removed URL.
DELETE FROM checks WHERE strpos(result_json, %(url)s) > 0;

-- name: share
-- Keeps an existing share_id, so sharing twice gives the same link.
UPDATE checks SET share_id = COALESCE(share_id, %(share_id)s), shared_at = COALESCE(shared_at, %(now)s)
WHERE check_id = %(check_id)s
RETURNING share_id, shared_at, created_at;

-- name: unshare
UPDATE checks SET share_id = NULL, shared_at = NULL WHERE check_id = %(check_id)s RETURNING check_id;

-- name: by_share
SELECT share_id, created_at, shared_at, status, result_json, corpus_version, model
FROM checks WHERE share_id = %(share_id)s;
