-- name: insert
INSERT INTO checks (check_id, created_at, input_masked, status, result_json, corpus_version,
                    model, embedding_model, duration_ms)
VALUES (%(check_id)s, %(created_at)s, %(input_masked)s, %(status)s, %(result_json)s,
        %(corpus_version)s, %(model)s, %(embedding_model)s, %(duration_ms)s);

-- name: logged_text
SELECT input_masked, result_json FROM checks WHERE check_id = %(check_id)s;

-- name: list
SELECT check_id, created_at, status, result_json, corpus_version, model, embedding_model, duration_ms
FROM checks
ORDER BY created_at DESC;
