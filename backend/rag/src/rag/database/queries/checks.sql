-- name: insert
INSERT INTO checks (check_id, created_at, input_masked, status, result_json, corpus_version,
                    model, embedding_model, duration_ms, channel, outcome, error_type,
                    llm_calls, input_tokens, output_tokens, cost, cost_currency)
VALUES (%(check_id)s, %(created_at)s, %(input_masked)s, %(status)s, %(result_json)s,
        %(corpus_version)s, %(model)s, %(embedding_model)s, %(duration_ms)s, %(channel)s,
        %(outcome)s, %(error_type)s, %(llm_calls)s, %(input_tokens)s, %(output_tokens)s,
        %(cost)s, %(cost_currency)s);

-- name: set_derived
-- Backfill of the derived columns (schema v5) from result_json.
UPDATE checks SET outcome = %(outcome)s, error_type = %(error_type)s, llm_calls = %(llm_calls)s,
                  input_tokens = %(input_tokens)s, output_tokens = %(output_tokens)s,
                  cost = %(cost)s, cost_currency = %(cost_currency)s
WHERE check_id = %(check_id)s;

-- name: logged_text
SELECT input_masked, result_json FROM checks WHERE check_id = %(check_id)s;

-- name: list
SELECT check_id, created_at, status, result_json, corpus_version, model, embedding_model, duration_ms
FROM checks
ORDER BY created_at DESC;

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

-- name: export
-- Analyst export (rag/export.py): no input text and no keys (check_id is hashed in Python,
-- share_id is reduced to a flag). Time filters and ordering run in Python (UTC ISO text).
SELECT check_id, created_at, status, result_json, corpus_version, model, embedding_model,
       duration_ms, share_id IS NOT NULL AS shared
FROM checks;

-- name: page
-- Admin history, newest first, keyset pagination on (created_at, check_id). Every filter is
-- optional (NULL = no filter). created_at is ISO 8601 UTC text from rag.timeutil.utcnow_iso,
-- one fixed format, so text comparison orders it correctly.
SELECT c.check_id, c.created_at, c.input_masked, c.status, c.channel, c.outcome, c.error_type,
       c.model, c.duration_ms, c.llm_calls, c.input_tokens, c.output_tokens, c.cost,
       c.cost_currency, r.label AS review_label
FROM checks c
LEFT JOIN check_reviews r USING (check_id)
WHERE (%(before_created)s::TEXT IS NULL OR (c.created_at, c.check_id) < (%(before_created)s, %(before_id)s))
  AND (%(since)s::TEXT IS NULL OR c.created_at >= %(since)s)
  AND (%(until)s::TEXT IS NULL OR c.created_at < %(until)s)
  AND (%(channel)s::TEXT IS NULL OR c.channel = %(channel)s)
  AND (%(status)s::TEXT IS NULL OR c.status = %(status)s)
  AND (%(outcomes)s::TEXT[] IS NULL OR c.outcome = ANY(%(outcomes)s))
  AND (%(q)s::TEXT IS NULL OR strpos(lower(c.input_masked), lower(%(q)s)) > 0)
  AND (NOT %(unreviewed)s OR r.check_id IS NULL)
ORDER BY c.created_at DESC, c.check_id DESC
LIMIT %(limit)s;

-- name: get
SELECT c.*, r.label AS review_label, r.note AS review_note, r.reviewed_at
FROM checks c
LEFT JOIN check_reviews r USING (check_id)
WHERE c.check_id = %(check_id)s;

-- name: in_range
-- Rows for usage, failure and overview aggregates (grouped in Python by Warsaw day).
SELECT c.check_id, c.created_at, c.status, c.channel, c.outcome, c.error_type, c.model,
       c.duration_ms, c.llm_calls, c.input_tokens, c.output_tokens, c.cost, c.cost_currency,
       r.check_id IS NOT NULL AS reviewed
FROM checks c
LEFT JOIN check_reviews r USING (check_id)
WHERE c.created_at >= %(since)s AND (%(until)s::TEXT IS NULL OR c.created_at < %(until)s)
ORDER BY c.created_at;

-- name: usage_by_call
-- result_json of checks in a range that made LLM calls (per-call breakdown lives there).
SELECT created_at, model, result_json FROM checks
WHERE created_at >= %(since)s AND (%(until)s::TEXT IS NULL OR created_at < %(until)s)
  AND COALESCE(llm_calls, 0) > 0;

-- name: review_upsert
INSERT INTO check_reviews (check_id, label, note, reviewed_at)
VALUES (%(check_id)s, %(label)s, %(note)s, %(reviewed_at)s)
ON CONFLICT (check_id) DO UPDATE SET label = excluded.label, note = excluded.note,
                                     reviewed_at = excluded.reviewed_at;

-- name: review_delete
DELETE FROM check_reviews WHERE check_id = %(check_id)s;
