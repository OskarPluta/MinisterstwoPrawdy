"""Lista usuniętych treści (`removed.yaml` obok `sources.yaml`).

Wpis to adres dokumentu (`url`) albo całe źródło (`source`), zawsze z datą i powodem, bo lista
jest zarazem rejestrem usunięć. Crawler nie zapisuje już takich dokumentów, `crawler forget`
wycina je z `documents.jsonl`, a RAG pomija je przy `ingest` i kasuje z bazy (`rag purge`).
Procedura: docs/DANE_I_LICENCJE.md#procedura-usunięcia.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

REMOVALS_FILE = "removed.yaml"


class RemovalsError(ValueError):
    """Błędny wpis w removed.yaml."""


@dataclass(frozen=True)
class Removals:
    urls: frozenset[str] = frozenset()
    sources: frozenset[str] = frozenset()
    entries: tuple[dict[str, Any], ...] = field(default=(), compare=False)

    def __bool__(self) -> bool:
        return bool(self.urls or self.sources)

    def matches(self, *, url: str | None = None, source: str | None = None) -> bool:
        return (url is not None and url in self.urls) or (source is not None and source in self.sources)


def load_removals(path: str | Path) -> Removals:
    """Wczytuje listę; brak pliku to pusta lista. Każdy wpis musi mieć `url` albo `source`
    oraz `date` i `reason` (rejestr usunięć bez powodu nie spełnia procedury)."""
    path = Path(path)
    if not path.exists():
        return Removals()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    urls: set[str] = set()
    sources: set[str] = set()
    entries = []
    for i, e in enumerate(raw.get("removed") or []):
        if not isinstance(e, dict) or bool(e.get("url")) == bool(e.get("source")):
            raise RemovalsError(f"{path.name}: wpis #{i} musi mieć dokładnie jedno z pól 'url' albo 'source'")
        missing = [k for k in ("date", "reason") if not e.get(k)]
        if missing:
            raise RemovalsError(f"{path.name}: wpis #{i} nie ma pól {missing}")
        (urls if e.get("url") else sources).add(str(e.get("url") or e.get("source")).strip())
        entries.append(e)
    return Removals(frozenset(urls), frozenset(sources), tuple(entries))
