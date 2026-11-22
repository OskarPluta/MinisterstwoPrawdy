"""Hybrid retrieval: Postgres full-text search + dense vectors, merged with Reciprocal Rank Fusion."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import numpy as np

from .database import query as named_sql
from .db import Connection, corpus_version
from .embed import Embedder, _fold
from .reprints import ARTICLE_KINDS
from .timeutil import WARSAW, parse_ts

RRF_K = 60

# Very common Polish function words; they add noise to an OR query.
STOPWORDS = {
    "a", "aby", "ale", "albo", "bo", "by", "byc", "byl", "byla", "bylo", "czy", "dla", "do",
    "gdy", "i", "ich", "ile", "jak", "jaki", "jest", "juz", "lub", "ma", "na", "nad", "nie",
    "o", "od", "oraz", "po", "pod", "przez", "przy", "sa", "sie", "tak", "tam", "te", "to",
    "tu", "w", "we", "z", "za", "ze", "zas", "co", "ktory", "ktora", "ktore", "tylko", "jego",
    "jej", "tez", "the", "of",
}


@dataclass
class Filters:
    source_types: set[str] | None = None
    area: str | None = None  # matched against evidence areas (folded substring); records without areas pass
    since: datetime | None = None  # keep evidence whose validity/publication ends at or after this
    until: datetime | None = None  # ... and starts at or before this
    kinds: set[str] | None = None

    def __post_init__(self) -> None:
        # Stored times are aware; read a naive bound as Warsaw time instead of failing to compare.
        if self.since and self.since.tzinfo is None:
            self.since = self.since.replace(tzinfo=WARSAW)
        if self.until and self.until.tzinfo is None:
            self.until = self.until.replace(tzinfo=WARSAW)


@dataclass
class Hit:
    passage_id: int
    evidence_id: int
    score: float
    ranks: dict[str, int] = field(default_factory=dict)  # {"bm25": 3, "dense": 1}
    meta: dict[str, Any] = field(default_factory=dict)


def query_tokens(query: str) -> list[str]:
    # Folded like the indexed text (lowercase, no diacritics, "ł" -> "l").
    toks = [t for t in re.findall(r"\w+", _fold(query)) if len(t) > 1 and t not in STOPWORDS]
    seen: set[str] = set()
    return [t for t in toks if not (t in seen or seen.add(t))]


def fts_query(text: str, prefix_stem: bool = True) -> str | None:
    """A Postgres tsquery: OR of tokens. With prefix_stem, long tokens become prefix
    queries (a crude stand-in for a Polish stemmer: "Mazowszu" -> mazow:*).

    Tokens are \\w+ runs, so they cannot carry tsquery operators; the string is still
    passed to to_tsquery() as a bound parameter."""
    parts = []
    for t in query_tokens(text):
        if prefix_stem and len(t) >= 6 and not t.isdigit():
            parts.append(f"{t[: max(5, len(t) - 3)]}:*")
        else:
            parts.append(t)
    return " | ".join(parts) if parts else None


def _passes(meta: dict[str, Any], f: Filters | None) -> bool:
    if f is None:
        return True
    if f.source_types and meta["source_type"] not in f.source_types:
        return False
    if f.kinds and meta["kind"] not in f.kinds:
        return False
    if f.area:
        areas = [_fold(a) for a in meta["areas"]]
        if areas and not any(_fold(f.area) in a for a in areas):
            return False
    start = parse_ts(meta["valid_from"] or meta["observed_at"] or meta["published_at"])
    end = parse_ts(meta["valid_to"]) or start
    if f.since and end and end < f.since:
        return False
    return not (f.until and start and start > f.until)


def passage_meta(conn: Connection, ids: Iterable[int]) -> dict[int, dict[str, Any]]:
    ids = list(ids)
    if not ids:
        return {}
    rows = conn.execute(named_sql("passages.meta_by_ids"), {"ids": ids}).fetchall()
    out = {}
    for r in rows:
        d = dict(r)
        d["areas"] = json.loads(d.pop("areas_json"))
        d["fields"] = json.loads(d.pop("fields_json"))
        out[d["passage_id"]] = d
    return out


def rrf(ranked_lists: dict[str, list[int]], k: int = RRF_K) -> list[tuple[int, float, dict[str, int]]]:
    """Reciprocal Rank Fusion: score(d) = sum over lists of 1/(k + rank), ranks from 1."""
    scores: dict[int, float] = {}
    ranks: dict[int, dict[str, int]] = {}
    for name, ids in ranked_lists.items():
        for rank, pid in enumerate(ids, start=1):
            if name in ranks.setdefault(pid, {}):
                continue  # duplicate within one list: keep its best rank
            scores[pid] = scores.get(pid, 0.0) + 1.0 / (k + rank)
            ranks[pid][name] = rank
    order = sorted(scores, key=lambda pid: (-scores[pid], min(ranks[pid].values())))
    return [(pid, scores[pid], ranks[pid]) for pid in order]


class Retriever:
    def __init__(self, conn: Connection, embedder: Embedder | None, prefix_stem: bool = True):
        self.conn = conn
        self.embedder = embedder
        self.prefix_stem = prefix_stem
        self._matrix: np.ndarray | None = None
        self._ids: np.ndarray | None = None
        self._loaded_version: int | None = None

    # ---- dense index -------------------------------------------------------------
    def _ensure_matrix(self) -> None:
        v = corpus_version(self.conn)
        if self._loaded_version == v:
            return
        assert self.embedder is not None
        rows = self.conn.execute(named_sql("passages.current_embeddings")).fetchall()
        wrong = {r["embedding_model"] for r in rows if r["embedding_model"] != self.embedder.name}
        if wrong:
            raise RuntimeError(
                f"passages embedded with {sorted(map(str, wrong))}, query embedder is "
                f"{self.embedder.name!r}; run `rag reembed`"
            )
        self._ids = np.array([r["passage_id"] for r in rows], dtype=np.int64)
        self._matrix = (
            np.stack([np.frombuffer(r["embedding"], dtype=np.float32) for r in rows])
            if rows else np.zeros((0, 1), dtype=np.float32)
        )
        self._loaded_version = v

    # ---- single searches -----------------------------------------------------------
    def bm25(self, query: str, k: int = 20, filters: Filters | None = None) -> list[int]:
        q = fts_query(query, self.prefix_stem)
        if q is None:
            return []
        rows = self.conn.execute(
            named_sql("passages.search_fts"), {"query": q, "limit": k * 5 if filters else k}
        ).fetchall()
        ids = [r["passage_id"] for r in rows]
        return self._filter(ids, filters)[:k]

    def dense(self, query: str, k: int = 20, filters: Filters | None = None) -> list[int]:
        if self.embedder is None:
            return []
        self._ensure_matrix()
        assert self._matrix is not None and self._ids is not None
        if len(self._ids) == 0:
            return []
        sims = self._matrix @ self.embedder.embed_query(query)
        n = min(len(sims), k * 5 if filters else k)
        top = np.argpartition(-sims, n - 1)[:n]
        top = top[np.argsort(-sims[top])]
        return self._filter([int(self._ids[i]) for i in top], filters)[:k]

    def _filter(self, ids: list[int], filters: Filters | None) -> list[int]:
        if not filters or not ids:
            return ids
        meta = passage_meta(self.conn, ids)
        return [i for i in ids if i in meta and _passes(meta[i], filters)]

    # ---- fusion -------------------------------------------------------------------------
    def hybrid(
        self,
        queries: str | list[str],
        filters: Filters | None = None,
        k_each: int = 20,
        shortlist: int = 8,
        per_group: int = 2,
        mode: str = "hybrid",  # "hybrid" | "bm25" | "dense"
        per_article: int = 1,
    ) -> list[Hit]:
        """Run BM25 and dense search for each query, fuse all lists with RRF, and
        return a diverse shortlist: at most `per_group` passages per evidence /
        syndication group, and at most `per_article` per article or report (its second
        passage rarely adds evidence and crowds out other sources; scripts/eval_retrieval.py
        showed no loss at 1). An original and its reprint can still both appear."""
        if isinstance(queries, str):
            queries = [queries]
        lists: dict[str, list[int]] = {}
        for i, q in enumerate(queries):
            if mode in ("hybrid", "bm25"):
                lists[f"bm25#{i}"] = self.bm25(q, k_each, filters)
            if mode in ("hybrid", "dense"):
                lists[f"dense#{i}"] = self.dense(q, k_each, filters)
        fused = rrf(lists)
        meta = passage_meta(self.conn, [pid for pid, _, _ in fused])

        hits: list[Hit] = []
        per: dict[str, int] = {}
        per_record: dict[int, int] = {}
        for pid, score, ranks in fused:
            m = meta[pid]
            group = m["syndicated_from"] or f"{m['source_id']}:{m['upstream_id']}"
            if per.get(group, 0) >= per_group:
                continue
            if m["kind"] in ARTICLE_KINDS and per_record.get(m["evidence_id"], 0) >= per_article:
                continue
            per[group] = per.get(group, 0) + 1
            per_record[m["evidence_id"]] = per_record.get(m["evidence_id"], 0) + 1
            hits.append(Hit(pid, m["evidence_id"], score, ranks, m))
            if len(hits) >= shortlist:
                break
        return hits
