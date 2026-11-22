"""Turn evidence records into searchable passages.

Articles: split by section, then at paragraph / sentence boundaries so that the
text actually embedded ("passage: " + title + heading + body) stays within
~MAX_TOKENS tokens of the embedding model (e5 reads at most 512).
Structured records: render a fixed Polish template from typed fields.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from typing import Any

from .db import PassageIn
from .timeutil import fmt_local

TokenCounter = Callable[[str], int]

MAX_TOKENS = 400  # target per passage, including the "passage: " prefix, title and heading
MIN_TAIL_TOKENS = 100  # a trailing chunk shorter than this is merged into the previous one
# A merged tail may overshoot MAX_TOKENS by MIN_TAIL_TOKENS (500), still below e5's 512.

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def estimate_tokens(text: str) -> int:
    """Offline, conservative stand-in for the e5 tokenizer (used with --embedder hash).

    On the Polish snapshot e5 gives ~1.9 tokens per word (p90 2.25) and ~4.2 chars
    per token (p10 3.5); taking the larger of both estimates rarely undercounts.
    """
    return 4 + max(math.ceil(len(text.split()) * 2.2), math.ceil(len(text) / 3.4))


def _split_words(piece: str, fits: Callable[[str], bool]) -> list[str]:
    """Last resort for one sentence that alone is over budget: cut between words."""
    out: list[str] = []
    buf: list[str] = []
    for word in piece.split():
        if buf and not fits(" ".join(buf + [word])):
            out.append(" ".join(buf))
            buf = []
        buf.append(word)
    if buf:
        out.append(" ".join(buf))
    return out


def _split_long_paragraph(par: str, fits: Callable[[str], bool]) -> list[str]:
    out: list[str] = []
    buf: list[str] = []
    for sent in _SENTENCE_SPLIT.split(par):
        if not fits(sent):
            if buf:
                out.append(" ".join(buf))
                buf = []
            out.extend(_split_words(sent, fits))
            continue
        if buf and not fits(" ".join(buf + [sent])):
            out.append(" ".join(buf))
            buf = []
        buf.append(sent)
    if buf:
        out.append(" ".join(buf))
    return out


def split_section(
    text: str,
    count: TokenCounter | None = None,
    max_tokens: int = MAX_TOKENS,
    min_tail: int = MIN_TAIL_TOKENS,
) -> list[str]:
    """Greedy packing of whole paragraphs (long ones cut at sentences) up to
    `max_tokens` as measured by `count` on the chunk text. `count` should measure
    the full embedded text (see `chunk_record`); defaults to `estimate_tokens`."""
    count = count or estimate_tokens
    max_tokens = max(max_tokens, count("") + min_tail)  # absurdly long title: still make progress
    fits = lambda s: count(s) <= max_tokens  # noqa: E731

    paragraphs: list[str] = []
    for par in re.split(r"\n\s*\n", text.strip()):
        par = par.strip()
        if not par:
            continue
        paragraphs.extend([par] if fits(par) else _split_long_paragraph(par, fits))

    chunks: list[str] = []
    buf: list[str] = []
    for par in paragraphs:
        if buf and not fits("\n\n".join(buf + [par])):
            chunks.append("\n\n".join(buf))
            buf = []
        buf.append(par)
    if buf:
        tail = "\n\n".join(buf)
        merged = chunks[-1] + "\n\n" + tail if chunks else ""
        # count() includes the fixed prefix/title overhead, so compare the tail's own share
        overhead = count("")
        if chunks and count(tail) - overhead < min_tail and count(merged) <= max_tokens + min_tail:
            chunks[-1] = merged
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
    if kind == "weather_reading":
        values = [("temperatura powietrza", f.get("temperature"), "°C"), ("prędkość wiatru", f.get("wind_speed"), " m/s"),
                  ("kierunek wiatru", f.get("wind_direction"), "°"), ("wilgotność względna", f.get("humidity"), "%"),
                  ("suma opadu", f.get("precipitation"), " mm"), ("ciśnienie", f.get("pressure"), " hPa")]
        return (
            f"Pomiar synoptyczny {f.get('issuer', 'IMGW-PIB')}. Stacja: {f['station']}. "
            f"Czas pomiaru: {fmt_local(record.get('observed_at'))}. "
            + "; ".join(f"{name}: {_num(v)}{unit}" for name, v, unit in values if v is not None) + "."
        )
    if kind == "air_quality":
        per = ", ".join(f"{p}: {cat}" for p, cat in (f.get("pollutants") or {}).items())
        return (
            f"Indeks jakości powietrza {f.get('issuer', 'GIOŚ')}. Stacja: {f['station']} ({areas}). "
            f"Obliczono: {fmt_local(record.get('observed_at'))}. Indeks ogólny: {f['index']}."
            + (f" Poszczególne zanieczyszczenia: {per}." if per else "")
        )
    raise ValueError(f"no template for structured kind {kind!r}")


def _num(v: float | int) -> str:
    """1.4 -> "1,4" (Polish decimal comma), 3.0 -> "3"."""
    return f"{v:g}".replace(".", ",")


# Typed fields each structured kind must carry (the templates above read them directly).
REQUIRED_FIELDS = {
    "warning_meteo": ("phenomenon", "level"),
    "warning_hydro": ("phenomenon", "level"),
    "weather_reading": ("station",),
    "air_quality": ("station", "index"),
}
STRUCTURED_KINDS = set(REQUIRED_FIELDS)


def chunk_record(record: dict[str, Any], count_tokens: TokenCounter | None = None) -> list[PassageIn]:
    """Raises ValueError for a record that cannot be turned into passages.

    `count_tokens(text)` gives the embedding model's token count for the text it
    embeds (prefix included); without it a word/char estimate is used."""
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

        def count(chunk: str, heading: str | None = heading) -> int:
            return (count_tokens or estimate_tokens)(embedding_text(record.get("title") or "", heading, chunk))

        for chunk in split_section(section["text"], count):
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
