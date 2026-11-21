"""Retrieval metrics over a labelled case file (recall@k and MRR, per tag)."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from .search import Filters, Retriever


def load_cases(path: str | Path) -> dict[str, Any]:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def key(meta: dict[str, Any]) -> str:
    return f"{meta['source_id']}:{meta['upstream_id']}"


def _filters(d: dict[str, Any] | None) -> Filters | None:
    if not d:
        return None
    return Filters(
        source_types=set(d["source_types"]) if d.get("source_types") else None,
        kinds=set(d["kinds"]) if d.get("kinds") else None,
        area=d.get("area"),
        since=datetime.fromisoformat(d["since"]) if d.get("since") else None,
        until=datetime.fromisoformat(d["until"]) if d.get("until") else None,
    )


def evaluate_retrieval(
    retriever: Retriever, cases: list[dict[str, Any]], mode: str, k: int = 8, cutoffs=(1, 3)
) -> dict:
    per_case: list[dict[str, Any]] = []
    for c in cases:
        hits = retriever.hybrid(c["query"], filters=_filters(c.get("filters")), shortlist=k, mode=mode)
        keys = [key(h.meta) for h in hits]
        gold = set(c["gold"])
        rank = next((i for i, kk in enumerate(keys, 1) if kk in gold), None)
        excluded_ok = not (set(c.get("must_exclude", [])) & set(keys))
        per_case.append({
            "id": c["id"], "tags": c["tags"], "hit": rank is not None and excluded_ok,
            "rr": (1.0 / rank) if rank else 0.0, "rank": rank, "top": keys[:3],
        })

    by_tag: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in per_case:
        by_tag["ALL"].append(r)
        for t in r["tags"]:
            by_tag[t].append(r)
    summary = {
        t: {
            "n": len(rs),
            **{
                f"recall@{c}": sum(r["hit"] and r["rank"] <= c for r in rs) / len(rs)
                for c in cutoffs
            },
            f"recall@{k}": sum(r["hit"] for r in rs) / len(rs),
            "mrr": sum(r["rr"] for r in rs) / len(rs),
        }
        for t, rs in sorted(by_tag.items())
    }
    return {"summary": summary, "cases": per_case}
