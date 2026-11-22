-- Upgrade steps for databases created by an older SCHEMA_VERSION (rag/db.py:connect).
-- Columns added to existing tables must be added here as well as in schema.sql: schema.sql
-- only creates missing tables, and its indexes may refer to the new columns.

-- name: v5_columns
ALTER TABLE sources  ADD COLUMN IF NOT EXISTS enabled BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE sources  ADD COLUMN IF NOT EXISTS admin_publisher TEXT;
ALTER TABLE sources  ADD COLUMN IF NOT EXISTS admin_source_type TEXT;
ALTER TABLE sources  ADD COLUMN IF NOT EXISTS admin_note TEXT;
ALTER TABLE sources  ADD COLUMN IF NOT EXISTS admin_updated_at TEXT;
ALTER TABLE evidence ADD COLUMN IF NOT EXISTS withdrawn_at TEXT;
ALTER TABLE evidence ADD COLUMN IF NOT EXISTS withdrawn_note TEXT;
ALTER TABLE checks   ADD COLUMN IF NOT EXISTS channel TEXT;
ALTER TABLE checks   ADD COLUMN IF NOT EXISTS outcome TEXT;
ALTER TABLE checks   ADD COLUMN IF NOT EXISTS error_type TEXT;
ALTER TABLE checks   ADD COLUMN IF NOT EXISTS llm_calls INTEGER;
ALTER TABLE checks   ADD COLUMN IF NOT EXISTS input_tokens INTEGER;
ALTER TABLE checks   ADD COLUMN IF NOT EXISTS output_tokens INTEGER;
ALTER TABLE checks   ADD COLUMN IF NOT EXISTS cost DOUBLE PRECISION;
ALTER TABLE checks   ADD COLUMN IF NOT EXISTS cost_currency TEXT;

-- name: checks_without_outcome
SELECT check_id, status, result_json, model FROM checks WHERE outcome IS NULL;
