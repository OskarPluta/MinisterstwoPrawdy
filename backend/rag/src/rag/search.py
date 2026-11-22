"""Hybrid retrieval: Postgres full-text search + dense vectors, merged with Reciprocal Rank Fusion."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from typing import Any

import numpy as np

from database import query as named_sql

from .areas import area_matches, area_score
from .db import Connection, corpus_version
from .embed import Embedder, _fold
from .reprints import ARTICLE_KINDS
from .timeutil import WARSAW, parse_ts

RRF_K = 60
# Weight of each soft list (claim area, claim period) in the fusion, relative to one search list.
SOFT_WEIGHT = 1.0
# Records with a validity window (warnings, RSO alerts). Together with structured measurements
# they describe their own period only; articles also report on earlier events.
TIMED_KINDS = {"warning_meteo", "warning_hydro", "alert"}  # alert: komunikaty RSO
# Two records of one source are about the same matter when their titles share this share of
# word stems (and kind, phenomenon/river/station and areas agree).
SAME_MATTER_TITLE_MIN = 0.6

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
class Prefer:
    """Soft ranking preferences from the claim: its area and period. Each becomes one more RRF
    list over the candidates the searches already found, so matching records move up, nothing
    is dropped and nothing new is added (see Retriever.hybrid)."""

    area: str | None = None  # graded by areas.area_score
    since: datetime | None = None  # the claim's period starts
    until: datetime | None = None  # ... and ends; applied to timed and structured records only
    now: datetime | None = None  # articles about a future period are published before it

    def __post_init__(self) -> None:
        for name in ("since", "until", "now"):
            v = getattr(self, name)
            if v is not None and v.tzinfo is None:
                setattr(self, name, v.replace(tzinfo=WARSAW))

    def __bool__(self) -> bool:
        return bool((self.area and self.area.strip()) or self.since or self.until)


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


def span(meta: dict[str, Any]) -> tuple[datetime | None, datetime | None]:
    """(start, end) of what a record describes: its validity window, else its observation or
    publication time (a point)."""
    start = parse_ts(meta["valid_from"] or meta["observed_at"] or meta["published_at"])
    return start, parse_ts(meta["valid_to"]) or start


def is_timed(meta: dict[str, Any]) -> bool:
    """A warning, alert or measurement: it is evidence only for its own period."""
    return meta["kind"] in TIMED_KINDS or meta["source_type"] == "structured"


def recorded_at(meta: dict[str, Any]) -> datetime | None:
    """When the record was issued (publication, else observation, else start of validity)."""
    return parse_ts(meta["published_at"] or meta["observed_at"] or meta["valid_from"])


def _passes(meta: dict[str, Any], f: Filters | None) -> bool:
    if f is None:
        return True
    if f.source_types and meta["source_type"] not in f.source_types:
        return False
    if f.kinds and meta["kind"] not in f.kinds:
        return False
    if f.area and area_matches(f.area, meta["areas"]) is False:
        return False
    start, end = span(meta)
    if f.since and end and end < f.since:
        return False
    return not (f.until and start and start > f.until)


def in_period(meta: dict[str, Any], p: Prefer) -> bool:
    """Whether a record fits the claim's period (soft ranking only). The end of the period
    (`until`) applies to timed and structured records only: a later article may well report on
    the period. For a period after `now`, `since` does not bind articles either."""
    start, end = span(meta)
    if start is None or end is None:
        return False
    timed = is_timed(meta)
    # A period still ahead is announced by articles published before it: `since` then binds
    # only timed and structured records.
    if p.since and (timed or p.now is None or p.since <= p.now) and end < p.since:
        return False
    return not (p.until and timed and start > p.until)


def lineage(meta: dict[str, Any]) -> str:
    """The source a record speaks for: a reprint speaks for its original's source."""
    sf = meta["syndicated_from"]
    return sf.split(":", 1)[0] if sf else meta["source_id"]


def _key(meta: dict[str, Any]) -> str:
    return f"{meta['source_id']}:{meta['upstream_id']}"


@lru_cache(maxsize=4096)
def _title_stems(title: str) -> frozenset[str]:
    return frozenset(t[:5] for t in query_tokens(title))


def same_matter(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Two distinct records of one source (or a reprint of it) about the same matter: e.g. an
    earlier and a later IMGW warning for one phenomenon and area, two readings of one station,
    or an article and its later update. A reprint and its original are one text, not two."""
    if a["evidence_id"] == b["evidence_id"] or a["kind"] != b["kind"] or lineage(a) != lineage(b):
        return False
    ka, kb = _key(a), _key(b)
    if ka == kb or a["syndicated_from"] == kb or b["syndicated_from"] == ka or (
        a["syndicated_from"] and a["syndicated_from"] == b["syndicated_from"]
    ):
        return False
    fa, fb = a["fields"] or {}, b["fields"] or {}
    for f in ("phenomenon", "river", "station"):
        if fa.get(f) and fb.get(f) and _fold(str(fa[f])) != _fold(str(fb[f])):
            return False
    ta, tb = _title_stems(a["title"] or ""), _title_stems(b["title"] or "")
    if a["areas"] or b["areas"]:
        # The same listed area (county, river stretch, station), not just one voivodeship:
        # two drought warnings for different rivers of one region are two matters.
        if not {_fold(x).strip() for x in a["areas"]} & {_fold(x).strip() for x in b["areas"]}:
            return False
    elif {t for t in ta if t.isdigit()} != {t for t in tb if t.isdigit()}:
        return False  # "Alert RCB (20.11)" and "(21.11)": numbered or dated items are distinct
    return bool(ta and tb) and len(ta & tb) / len(ta | tb) >= SAME_MATTER_TITLE_MIN


def supersessions(metas: Iterable[dict[str, Any]]) -> dict[int, int]:
    """{older evidence_id: newest evidence_id} among records about the same matter from the
    same source (same_matter). The newer record wins: it is ranked above the older one in
    `hybrid` and decides in verify.py."""
    by_ev: dict[int, dict[str, Any]] = {}
    for m in metas:
        by_ev.setdefault(m["evidence_id"], m)
    groups: dict[tuple[str, str], list[tuple[dict[str, Any], datetime]]] = {}
    for m in by_ev.values():
        t = recorded_at(m)
        if t is not None:
            groups.setdefault((lineage(m), m["kind"]), []).append((m, t))
    out: dict[int, int] = {}
    for recs in groups.values():
        for old, t_old in recs:
            best: tuple[datetime, int] | None = None
            for new, t_new in recs:
                if t_new > t_old and same_matter(old, new) and (best is None or (t_new, new["evidence_id"]) > best):
                    best = (t_new, new["evidence_id"])
            if best:
                out[old["evidence_id"]] = best[1]
    return out


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


def rrf(
    ranked_lists: dict[str, list[int]], k: int = RRF_K, weights: dict[str, float] | None = None,
) -> list[tuple[int, float, dict[str, int]]]:
    """Reciprocal Rank Fusion: score(d) = sum over lists of w/(k + rank), ranks from 1
    (w = weights[list], default 1)."""
    scores: dict[int, float] = {}
    ranks: dict[int, dict[str, int]] = {}
    for name, ids in ranked_lists.items():
        w = (weights or {}).get(name, 1.0)
        for rank, pid in enumerate(ids, start=1):
            if name in ranks.setdefault(pid, {}):
                continue  # duplicate within one list: keep its best rank
            scores[pid] = scores.get(pid, 0.0) + w / (k + rank)
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
        prefer: Prefer | None = None,
    ) -> list[Hit]:
        """Run BM25 and dense search for each query, fuse all lists with RRF, and
        return a diverse shortlist: at most `per_group` passages per evidence /
        syndication group, and at most `per_article` per article or report (its second
        passage rarely adds evidence and crowds out other sources; scripts/eval_retrieval.py
        showed no loss at 1). An original and its reprint can still both appear.

        `prefer` (the claim's area and period) adds soft lists: the candidates found so far
        that match the area ("area") or the period ("period"), in their fused order. They
        reorder candidates and never drop or add any. Last, a newer record of the same source
        about the same matter (supersessions) is ranked right above the older one."""
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
        if prefer:
            order = [pid for pid, _, _ in fused]
            soft: dict[str, list[int]] = {}
            if prefer.area and prefer.area.strip():
                scores = {p: area_score(prefer.area, meta[p]["areas"]) or 0 for p in order}
                # Same area first, then national-vs-regional, each in fused order (sort is stable).
                soft["area"] = sorted((p for p in order if scores[p] > 0), key=lambda p: -scores[p])
            if prefer.since or prefer.until:
                soft["period"] = [p for p in order if in_period(meta[p], prefer)]
            fused = rrf({**lists, **soft}, weights={n: SOFT_WEIGHT for n in soft})
        fused = _newer_first(fused, meta)

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


def _newer_first(
    fused: list[tuple[int, float, dict[str, int]]], meta: dict[int, dict[str, Any]],
) -> list[tuple[int, float, dict[str, int]]]:
    """Rank the newest record of a matter (supersessions) at least as high as the older
    records it supersedes; both stay in the list."""
    newer = supersessions(meta[pid] for pid, _, _ in fused)
    if not newer:
        return fused
    best: dict[int, float] = {}  # evidence_id -> best score of its passages
    for pid, score, _ in fused:
        ev = meta[pid]["evidence_id"]
        best[ev] = max(best.get(ev, score), score)
    lift: dict[int, float] = {}
    for old, new in newer.items():
        lift[new] = max(lift.get(new, 0.0), best[old])
    out = []
    for pid, score, ranks in fused:
        ev = meta[pid]["evidence_id"]
        if ev in lift and lift[ev] >= score:
            score = lift[ev] + 1e-9
        out.append((pid, score, ranks))
    return sorted(out, key=lambda t: (-t[1], min(t[2].values())))
