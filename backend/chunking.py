"""Turn evidence records into searchable passages.

Articles: split by section, then at paragraph boundaries (~150-300 words).
Structured records: render a fixed Polish template from typed fields.
"""

from __future__ import annotations

import re
from typing import Any

from .db import PassageIn
from .timeutil import fmt_local

MAX_WORDS = 300
MIN_TAIL_WORDS = 60  # a trailing chunk shorter than this is merged into the previous one

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _wc(s: str) -> int:
    return len(s.split())


def _split_long_paragraph(par: str, max_words: int) -> list[str]:
    out: list[str] = []
    buf: list[str] = []
    for sent in _SENTENCE_SPLIT.split(par):
        if buf and _wc(" ".join(buf + [sent])) > max_words:
            out.append(" ".join(buf))
            buf = []
        buf.append(sent)
    if buf:
        out.append(" ".join(buf))
    return out


def split_section(text: str, max_words: int = MAX_WORDS) -> list[str]:
    paragraphs: list[str] = []
    for par in re.split(r"\n\s*\n", text.strip()):
        par = par.strip()
        if not par:
            continue
        paragraphs.extend(_split_long_paragraph(par, max_words) if _wc(par) > max_words else [par])

    chunks: list[str] = []
    buf: list[str] = []
    for par in paragraphs:
        if buf and _wc("\n\n".join(buf + [par])) > max_words:
            chunks.append("\n\n".join(buf))
            buf = []
        buf.append(par)
    if buf:
        tail = "\n\n".join(buf)
        if chunks and _wc(tail) < MIN_TAIL_WORDS and _wc(chunks[-1]) + _wc(tail) <= max_words + MIN_TAIL_WORDS:
            chunks[-1] = chunks[-1] + "\n\n" + tail
        else:
            chunks.append(tail)
    return chunks


def _valid_to(record: dict[str, Any]) -> str:
    """End of validity; an open-ended warning shows its status (e.g. "obowiązuje do odwołania")."""
    if record.get("valid_to") or not record.get("status"):
        return fmt_local(record.get("valid_to"))
    return record["status"]


def render_structured(record: dict[str, Any]) -> str:
    """Fixed template per structured kind. Shown as a reading/warning, never quoted."""
    f = record.get("fields") or {}
    kind = record["kind"]
    areas = ", ".join(record.get("areas") or []) or "brak danych"
    if kind == "warning_meteo":
        return (
            f"Ostrzeżenie meteorologiczne {f.get('issuer', 'IMGW-PIB')}: {f['phenomenon']}. "
            f"Stopień: {f['level']}. Obszar: {areas}. "
            f"Obowiązuje: {fmt_local(record.get('valid_from'))} – {_valid_to(record)}. "
            f"Wydano: {fmt_local(record.get('published_at'))}."
            + (f" Prawdopodobieństwo: {f['probability']}%." if f.get("probability") else "")
            + (f" Opis: {f['description']}" if f.get("description") else "")
        )
    if kind == "warning_hydro":
        return (
            f"Ostrzeżenie hydrologiczne {f.get('issuer', 'IMGW-PIB')}: {f['phenomenon']}. "
            f"Stopień: {f['level']}. Rzeka: {f.get('river', 'brak danych')}. Obszar: {areas}. "
            f"Obowiązuje: {fmt_local(record.get('valid_from'))} – {_valid_to(record)}. "
            f"Wydano: {fmt_local(record.get('published_at'))}."
            + (f" Opis: {f['description']}" if f.get("description") else "")
        )
    if kind == "air_reading":
        return (
            f"Pomiar jakości powietrza GIOŚ. Stacja: {f['station']} ({areas}). "
            f"Zanieczyszczenie: {f['pollutant']}. Wartość: {f['value']} {f['unit']}. "
            f"Czas pomiaru: {fmt_local(record.get('observed_at'))}."
            + (f" Indeks jakości powietrza: {f['index']}." if f.get("index") else "")
        )
    if kind == "road_restriction":
        return (
            f"Utrudnienie drogowe GDDKiA. Droga: {f['road']}, odcinek: {f['section']}"
            + (f", kierunek: {f['direction']}" if f.get("direction") else "")
            + f". Obszar: {areas}. Rodzaj: {f['effect']}. Przyczyna: {f.get('cause', 'brak danych')}. "
            f"Od: {fmt_local(record.get('valid_from'))} do: {fmt_local(record.get('valid_to'))}."
        )
    raise ValueError(f"no template for structured kind {kind!r}")


# Typed fields each structured kind must carry (the templates above read them directly).
REQUIRED_FIELDS = {
    "warning_meteo": ("phenomenon", "level"),
    "warning_hydro": ("phenomenon", "level"),
    "air_reading": ("station", "pollutant", "value", "unit"),
    "road_restriction": ("road", "section", "effect"),
}
STRUCTURED_KINDS = set(REQUIRED_FIELDS)


def chunk_record(record: dict[str, Any]) -> list[PassageIn]:
    """Raises ValueError for a record that cannot be turned into passages."""
    kind = record["kind"]
    if kind in STRUCTURED_KINDS:
        fields = record.get("fields") or {}
        missing = [k for k in REQUIRED_FIELDS[kind] if fields.get(k) in (None, "")]
        if missing:
            raise ValueError(f"{kind} record lacks fields {missing}")
        return [PassageIn(ord=0, heading=None, text=render_structured(record), excerpt=None)]

    passages: list[PassageIn] = []
    for section in record.get("sections") or []:
        heading = section.get("heading")
        for chunk in split_section(section["text"]):
            passages.append(
                PassageIn(
                    ord=len(passages), heading=heading, text=chunk, excerpt=chunk,
                    page=section.get("page"),
                )
            )
    if not passages:
        raise ValueError(f"record {record['upstream_id']} has no sections")
    return passages


def embedding_text(title: str, heading: str | None, text: str) -> str:
    """Text fed to the embedding model: title + heading + body."""
    return "\n".join(x for x in (title, heading, text) if x)
