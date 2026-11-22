"""Reprint detection: near-duplicate articles from different sources share one origin.

A wire text (e.g. PAP) republished by several outlets must count as one source in
`verify.assess_strength`, which reads `syndicated_from`. Records rarely carry it, so
ingest detects reprints and stores the original in `reprint_signatures.reprint_of`
(`passages.meta_by_ids` returns it as `syndicated_from` unless the record sets one).

Method (deterministic, numpy only):
- text: the body passages of a current article (`kind` article/report, source type not
  `structured`), folded like the search index; shingles are runs of SHINGLE_WORDS words;
- a MinHash signature (NUM_PERM permutations) finds candidate pairs; candidates are then
  compared exactly on their shingle sets;
- two articles of different sources are linked when they are near-identical
  (Jaccard >= JACCARD_MIN) or when the later one is a shortened copy of the earlier one
  (CONTAINMENT_MIN of its shingles appear there). An earlier short text inside a later long
  one is a quotation or a digest, not a reprint, and is not linked;
- linked articles form a cluster; its earliest article (published_at, else fetched_at)
  is the original and every other member points to it.

Detection is incremental: only new signatures are compared against the current corpus,
and only the clusters they touch (plus clusters of versions that left search) are
recomputed. The result depends only on the set of current articles, not on ingest order.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np

from database import query

from .db import Connection, _bump_corpus_version
from .embed import _fold
from .timeutil import parse_ts

ARTICLE_KINDS = ("article", "report")
SHINGLE_WORDS = 5
NUM_PERM = 128
# Thresholds (checked on data/snapshot: no pair of distinct texts there exceeds Jaccard 0.36
# or containment 0.62; synthetic reprints with an added lead, light edits or truncation to
# half the text stay above them).
JACCARD_MIN = 0.5
CONTAINMENT_MIN = 0.8
MIN_SHINGLES = 20  # shorter texts (about 25 words) are too formulaic to call a reprint
CANDIDATE_MIN_MATCHES = 3  # equal MinHash values out of NUM_PERM to compare a pair exactly

_PRIME = (1 << 61) - 1
_rng = np.random.default_rng(20261122)
_A = _rng.integers(1, 1 << 32, NUM_PERM, dtype=np.uint64)
_B = _rng.integers(0, 1 << 32, NUM_PERM, dtype=np.uint64)
_MAX32 = np.uint64(0xFFFFFFFF)


def shingles(text: str) -> set[int]:
    """32-bit hashes of the word SHINGLE_WORDS-grams of `text` (folded, punctuation ignored)."""
    words = re.findall(r"\w+", _fold(text))
    n = SHINGLE_WORDS
    grams = (" ".join(words[i : i + n]) for i in range(len(words) - n + 1)) if len(words) >= n else ()
    return {int.from_bytes(hashlib.blake2b(g.encode(), digest_size=4).digest(), "little") for g in grams}


def minhash(sh: set[int]) -> np.ndarray:
    if not sh:
        return np.full(NUM_PERM, 0xFFFFFFFF, dtype=np.uint32)
    x = np.fromiter(sh, dtype=np.uint64, count=len(sh))[:, None]
    h = ((x * _A + _B) % np.uint64(_PRIME)) & _MAX32  # a, x < 2**32: no uint64 overflow
    return h.min(axis=0).astype(np.uint32)


@dataclass
class _Node:
    evidence_id: int
    key: str  # source_id:upstream_id
    source_id: str
    when: tuple[datetime, str]
    size: int
    reprint_of: str | None


def _when(published_at: str | None, fetched_at: str | None, key: str) -> tuple[datetime, str]:
    dt = parse_ts(published_at) or parse_ts(fetched_at) or datetime.max.replace(tzinfo=timezone.utc)
    return (dt, key)


def is_reprint_pair(a: set[int], b: set[int], a_first: bool) -> bool:
    """Whether two shingle sets of different sources are one text (see module docstring).
    `a_first`: `a` was published before `b`."""
    if min(len(a), len(b)) < MIN_SHINGLES:
        return False
    inter = len(a & b)
    if inter / len(a | b) >= JACCARD_MIN:
        return True
    later = b if a_first else a
    return len(later) <= len(a if a_first else b) and inter / len(later) >= CONTAINMENT_MIN


def _texts(conn: Connection, ids: Iterable[int]) -> dict[int, str]:
    out: dict[int, list[str]] = {}
    for r in conn.execute(query("reprints.texts"), {"ids": list(ids)}):
        out.setdefault(r["evidence_id"], []).append(r["search_text"])
    return {k: "\n".join(v) for k, v in out.items()}


def refresh(conn: Connection, *, recheck: bool = False) -> int:
    """Sign current articles that have no signature yet and recompute the reprint clusters
    they (or versions retired since the last run) touch. `recheck` also recomputes links whose
    original was deleted (rag purge). Idempotent; returns the number of articles whose
    detected original changed."""
    with conn.transaction():
        conn.execute(query("reprints.lock"))
        new_ids = [r["evidence_id"] for r in conn.execute(query("reprints.unsigned"), {"kinds": list(ARTICLE_KINDS)})]
        sets: dict[int, set[int]] = {}
        for eid, text in _texts(conn, new_ids).items():
            sets[eid] = shingles(text)
        for eid in new_ids:
            sh = sets.setdefault(eid, set())
            conn.execute(
                query("reprints.insert_signature"),
                {"evidence_id": eid, "shingles": len(sh), "minhash": minhash(sh).tobytes()},
            )
        retired = conn.execute(query("reprints.retired")).fetchall()
        if not new_ids and not retired and not recheck:
            return 0

        rows = conn.execute(query("reprints.current")).fetchall()
        nodes = []
        for r in rows:
            key = f"{r['source_id']}:{r['upstream_id']}"
            nodes.append(_Node(r["evidence_id"], key, r["source_id"],
                               _when(r["published_at"], r["fetched_at"], key), r["shingles"], r["reprint_of"]))
        index = {n.evidence_id: i for i, n in enumerate(nodes)}
        sig = (np.stack([np.frombuffer(r["minhash"], dtype=np.uint32) for r in rows])
               if rows else np.zeros((0, NUM_PERM), dtype=np.uint32))
        sources = np.array([n.source_id for n in nodes], dtype=object)
        usable = np.array([n.size >= MIN_SHINGLES for n in nodes], dtype=bool)

        def shingle_set(i: int) -> set[int]:
            eid = nodes[i].evidence_id
            if eid not in sets:
                sets.update({k: shingles(t) for k, t in _texts(conn, [eid]).items()})
                sets.setdefault(eid, set())
            return sets[eid]

        def neighbours(i: int, among: np.ndarray) -> list[int]:
            if not usable[i] or len(among) == 0:
                return []
            matches = (sig[among] == sig[i]).sum(axis=1)
            cand = among[(matches >= CANDIDATE_MIN_MATCHES) & usable[among] & (sources[among] != sources[i])]
            mine = shingle_set(i)
            return [int(j) for j in cand if j != i and is_reprint_pair(
                mine, shingle_set(int(j)), nodes[i].when < nodes[int(j)].when)]

        def root(i: int) -> str:
            return nodes[i].reprint_of or nodes[i].key

        everyone = np.arange(len(nodes))
        fresh = [index[e] for e in new_ids if e in index]
        touched = {f"{r['source_id']}:{r['upstream_id']}" for r in retired}
        touched |= {r["reprint_of"] for r in retired if r["reprint_of"]}
        keys = {n.key for n in nodes}
        touched |= {n.reprint_of for n in nodes if n.reprint_of and n.reprint_of not in keys}  # stale
        for i in fresh:
            touched.add(nodes[i].key)
            touched.update(root(j) for j in neighbours(i, everyone))
        group = np.array(sorted(
            {i for i, n in enumerate(nodes) if n.key in touched or n.reprint_of in touched} | set(fresh)
        ), dtype=np.int64)

        missing = [nodes[int(i)].evidence_id for i in group if nodes[int(i)].evidence_id not in sets]
        for eid, text in _texts(conn, missing).items():
            sets[eid] = shingles(text)
        parent = {int(i): int(i) for i in group}

        def find(i: int) -> int:
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        for i in group:
            for j in neighbours(int(i), group):
                parent[find(int(i))] = find(j)
        clusters: dict[int, list[int]] = {}
        for i in group:
            clusters.setdefault(find(int(i)), []).append(int(i))

        changed = 0
        for members in clusters.values():
            original = min(members, key=lambda m: nodes[m].when)
            for m in members:
                want = None if m == original else nodes[original].key
                if nodes[m].reprint_of != want:
                    conn.execute(query("reprints.set_reprint_of"),
                                 {"evidence_id": nodes[m].evidence_id, "reprint_of": want})
                    changed += 1
        if retired:
            conn.execute(query("reprints.deactivate"), {"ids": [r["evidence_id"] for r in retired]})
        if changed:
            _bump_corpus_version(conn)
        return changed
