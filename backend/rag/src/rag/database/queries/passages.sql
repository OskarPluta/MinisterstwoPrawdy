-- name: insert
-- *_fts parameters are folded (lowercase, no diacritics) by the application.
INSERT INTO passages (evidence_id, ord, heading, page, excerpt, search_text, is_verbatim,
                      embedding, embedding_model, fts)
VALUES (%(evidence_id)s, %(ord)s, %(heading)s, %(page)s, %(excerpt)s, %(search_text)s,
        %(is_verbatim)s, %(embedding)s, %(embedding_model)s,
        setweight(to_tsvector('simple', %(title_fts)s), 'A')
        || setweight(to_tsvector('simple', %(heading_fts)s), 'B')
        || setweight(to_tsvector('simple', %(text_fts)s), 'C'))
RETURNING passage_id;

-- name: unindex_evidence
-- Keep the passages for citation traceability but remove them from full-text search.
UPDATE passages SET fts = NULL WHERE evidence_id = %(evidence_id)s;

-- name: set_embedding
UPDATE passages SET embedding = %(embedding)s, embedding_model = %(model)s
WHERE passage_id = %(passage_id)s;

-- name: current_for_embedding
SELECT p.passage_id, p.heading, p.search_text, e.title
FROM passages p JOIN evidence e USING (evidence_id)
WHERE e.is_current;

-- name: current_embeddings
SELECT p.passage_id, p.embedding, p.embedding_model
FROM passages p JOIN evidence e USING (evidence_id)
WHERE e.is_current
ORDER BY p.passage_id;

-- name: search_fts
-- Weights are {D, C, B, A}: body 0.2, heading 0.6, title 1.0 (same 1:3:5 ratio as
-- the former FTS5 bm25 weights). Cover density without length normalization matched
-- the old BM25 numbers best on backend/rag/tests/fixtures/retrieval_cases.yaml (scripts/eval_retrieval.py).
SELECT passage_id
FROM passages, to_tsquery('simple', %(query)s) AS q
WHERE fts @@ q
ORDER BY ts_rank_cd('{0, 0.2, 0.6, 1.0}', fts, q, 0) DESC, passage_id
LIMIT %(limit)s;

-- name: meta_by_ids
-- syndicated_from: the record's own value, else the original found by reprint detection.
SELECT p.passage_id, p.evidence_id, p.heading, p.search_text, p.excerpt, p.is_verbatim, p.page,
       e.source_id, e.upstream_id, e.version, e.kind, e.title, e.citation_url, e.published_at,
       e.observed_at, e.valid_from, e.valid_to, e.areas_json, e.status,
       COALESCE(e.syndicated_from, r.reprint_of) AS syndicated_from,
       e.fields_json, s.publisher, s.publishing_domain, s.source_type, s.last_success_at,
       s.last_error, s.attribution, s.processed_notice
FROM passages p
JOIN evidence e USING (evidence_id)
JOIN sources s USING (source_id)
LEFT JOIN reprint_signatures r ON r.evidence_id = p.evidence_id
WHERE p.passage_id = ANY(%(ids)s);

-- name: searchable_by_evidence
-- Live lookup (rag/live.py): searchable passages of the given records, with vectors for ranking.
SELECT passage_id, evidence_id, embedding, embedding_model
FROM passages
WHERE evidence_id = ANY(%(ids)s) AND fts IS NOT NULL
ORDER BY evidence_id, ord;

-- name: delete_by_evidence
DELETE FROM passages WHERE evidence_id = ANY(%(ids)s);
