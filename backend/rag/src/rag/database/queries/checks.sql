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
