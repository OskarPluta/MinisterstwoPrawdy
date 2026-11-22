"""Area matching between a claim ("całe Mazowsze", "na Podlasiu", "w całej Polsce") and the
areas of a record ("powiat legionowski (mazowieckie)", "województwo lubelskie", "Polska").

A plain folded substring test misses Polish forms: "Mazowsze" is not inside "województwo
mazowieckie". Here each text is reduced to
- voivodeships named in it, in any form (adjective, region noun, inflected, without diacritics),
- whether it means the whole country ("Polska", "cały kraj", "RP"),
- the remaining place-name words, compared by a shared prefix (Kielce ~ Kielcach).

Used by `search._passes` (hard area filter) and by the soft area ranking list in
`search.Retriever.hybrid`. Cities are not mapped to voivodeships: "Warszawa" does not match a
warning for other counties of mazowieckie.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from .embed import _fold

# (canonical name, pattern on folded text). Compound names first: each match is removed
# before the next pattern runs, so "kujawsko-pomorskie" does not also count as "pomorskie".
VOIVODESHIPS: list[tuple[str, re.Pattern[str]]] = [
    (name, re.compile(rx))
    for name, rx in [
        ("kujawsko-pomorskie", r"\bkujaw\w*(?:[\s-]+pomor\w*)?"),
        ("warmińsko-mazurskie", r"\bwarmi\w*(?:[\s-]+mazur\w*)?|\bmazur\w*"),
        ("zachodniopomorskie", r"\bzachodniopomor\w*|\bpomorz\w*\s+zachodni\w*|\bzachodni\w*\s+pomorz\w*"),
        ("dolnośląskie", r"\bdolnoslas\w*|\bdoln\w*\s+slas\w*"),
        ("śląskie", r"\bslas\w*|\bgorn\w*\s+slas\w*"),
        ("pomorskie", r"\bpomor\w*"),
        ("mazowieckie", r"\bmazow(?:iec|sz)\w*"),
        ("podlaskie", r"\bpodlas\w*"),
        ("lubelskie", r"\blubel\w*"),
        ("lubuskie", r"\blubus\w*"),
        ("łódzkie", r"\blodzk\w*"),
        ("małopolskie", r"\bmalopol\w*"),
        ("opolskie", r"\bopol\w*"),
        ("podkarpackie", r"\bpodkarpac\w*"),
        ("świętokrzyskie", r"\bswietokrzys\w*|\bkielecczyzn\w*"),
        ("wielkopolskie", r"\bwielkopol\w*"),
    ]
]
_NATIONAL = re.compile(
    r"\bpolsk[aiey]?\b|\bpolsce\b|\brp\b|\bkraj\w*|\bogolnopolsk\w*|\bogolnokrajow\w*"
)
# Words that name the kind of area, not the place.
_GENERIC = {
    "powiat", "powiatu", "powiecie", "powiaty", "powiatach", "wojewodztwo", "wojewodztwie",
    "wojewodztwa", "gmina", "gminie", "gminy", "miasto", "miescie", "miasta", "rzeka", "rzece",
    "rzeki", "region", "regionie", "regionu", "cala", "cale", "calej", "calym", "caly", "calosci",
    "okolice", "okolicach", "okolic", "teren", "terenie", "obszar", "obszarze", "obszarach",
    "zachodni", "wschodni", "polnocny", "poludniowy", "zachodnie", "wschodnie", "polnocne",
    "poludniowe", "ziemski", "grodzki", "pod", "nad", "kolo", "przy", "oraz", "the", "and",
}


@dataclass(frozen=True)
class AreaKey:
    regions: frozenset[str]  # canonical voivodeship names
    national: bool
    words: frozenset[str]  # other place-name words, folded


@lru_cache(maxsize=4096)
def normalize_area(text: str) -> AreaKey:
    t = _fold(text or "")
    regions = set()
    for name, rx in VOIVODESHIPS:
        if rx.search(t):
            regions.add(name)
            t = rx.sub(" ", t)
    national = bool(_NATIONAL.search(t))
    t = _NATIONAL.sub(" ", t)
    words = {w for w in re.findall(r"\w+", t) if len(w) >= 3 and w not in _GENERIC and not w.isdigit()}
    return AreaKey(frozenset(regions), national, frozenset(words))


def _same_word(a: str, b: str) -> bool:
    """Inflected forms of one place name share a long prefix: Kielce/Kielcach, Łeba/Łebie,
    Legionowo/legionowski; Kraków/Kraśnik do not."""
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n >= max(3, min(len(a), len(b)) - 2)


# area_score grades
SAME_AREA = 2  # the same voivodeship or place (or both national)
WIDER_OR_NARROWER = 1  # one side is the whole country, the other a part of it


def _key_score(c: AreaKey, e: AreaKey) -> int:
    if c.national and e.national:
        return SAME_AREA
    if (c.national and e.regions) or (e.national and c.regions):
        return WIDER_OR_NARROWER  # the whole country covers each voivodeship
    if c.regions and e.regions:
        return SAME_AREA if c.regions & e.regions else 0  # other voivodeship: words do not count
    return SAME_AREA if any(_same_word(a, b) for a in c.words for b in e.words) else 0


def area_score(claim_area: str | None, evidence_areas: list[str]) -> int | None:
    """How well a record's areas fit the claim's area: SAME_AREA, WIDER_OR_NARROWER (a national
    record for a regional claim or the reverse), 0 (no overlap). None when either side is empty
    (unknown scope: neither a match nor a mismatch)."""
    if not claim_area or not claim_area.strip() or not evidence_areas:
        return None
    c = normalize_area(claim_area)
    folded = _fold(claim_area).strip()
    best = 0
    for a in evidence_areas:
        e = normalize_area(a)
        best = max(best, _key_score(c, e))
        # Plain containment only for names the voivodeship patterns do not cover ("pomorskie"
        # is inside "zachodniopomorskie", but it is another voivodeship).
        if not (c.regions or c.national or e.regions or e.national) and folded in _fold(a):
            best = SAME_AREA
        if best == SAME_AREA:
            break
    return best


def area_matches(claim_area: str | None, evidence_areas: list[str]) -> bool | None:
    """Whether a record's areas overlap the claim's area (area_score > 0). None when either
    side is empty."""
    score = area_score(claim_area, evidence_areas)
    return None if score is None else score > 0
