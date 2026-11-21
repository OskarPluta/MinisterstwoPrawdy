"""Maskowanie danych osobowych (RODO) przed zapisem: osoby → [OSOBA], PESEL → [PESEL].

NER (spaCy, etykieta persName) + dwa wzmocnienia:
 * wejście do NER jest normalizowane bez zmiany długości (offsety się zgadzają): separatory typu „/”
   zamieniamy na spacje („Anna Sakowska/KPP”), a linie PISANE WERSALIKAMI – na Title Case,
 * nazwiska/imiona znalezione gdziekolwiek w dokumencie maskujemy też w pozostałych miejscach
   (np. ten sam człowiek w nagłówku wersalikami).
Wolimy zamaskować za dużo niż za mało.
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger(__name__)

# 11 cyfr, niebędące częścią dłuższego ciągu cyfr; numery z prefiksem "+" (np. +48...) to telefony
PESEL_RE = re.compile(r"(?<![\d+])\d{11}(?!\d)")
MASK = "[OSOBA]"
_PIPES_NOT_NEEDED = ["parser", "lemmatizer", "tagger", "morphologizer", "attribute_ruler", "senter",
                     "trainable_lemmatizer"]
_SEPARATORS = str.maketrans({c: " " for c in "/|_\\•·"})
# słowa, które NER czasem oznacza jako osobę, a nie wolno ich propagować po dokumencie
_NO_PROPAGATE = {"policja", "policji", "komendant", "komendanta", "polska", "polski", "asp", "podkom",
                 "nadkom", "kom", "sierż", "st", "mł", "insp", "nadinsp", "mjr", "płk", "gen", "pan", "pani"}


class PiiUnavailable(RuntimeError):
    pass


def _ner_input(para: str) -> str:
    s = para.translate(_SEPARATORS)
    letters = [c for c in s if c.isalpha()]
    if len(letters) >= 8 and sum(c.isupper() for c in letters) / len(letters) > 0.8:
        t = s.title()
        if len(t) == len(s):  # title() prawie zawsze zachowuje długość; jeśli nie – zostaw
            s = t
    return s


class PiiMasker:
    def __init__(self, models: list[str]):
        import spacy  # noqa: F401  (import tu – brak spaCy też ma blokować start)

        last_err: Exception | None = None
        self.nlp = None
        for name in models:
            try:
                self.nlp = spacy.load(name, exclude=_PIPES_NOT_NEEDED)
                self.model_name = name
                break
            except Exception as e:  # OSError (brak modelu), ImportError itd.
                last_err = e
        if self.nlp is None:
            raise PiiUnavailable(f"Nie można załadować modelu spaCy ({', '.join(models)}): {last_err}")
        if "ner" not in self.nlp.pipe_names:
            raise PiiUnavailable(f"Model {self.model_name} nie ma komponentu NER")
        self.nlp.max_length = max(self.nlp.max_length, 2_000_000)
        log.info("Maskowanie PII: model %s", self.model_name)

    def _spans(self, para: str) -> list[tuple[int, int]]:
        doc = self.nlp(_ner_input(para))
        return [(e.start_char, e.end_char) for e in doc.ents if e.label_ == "persName"]

    def mask(self, text: str | None, also_names: set[str] | None = None) -> str | None:
        if not text:
            return text
        paras = text.split("\n")
        all_spans: list[list[tuple[int, int]]] = []
        names: set[str] = set(also_names or ())
        for para in paras:
            spans = self._spans(para) if para.strip() else []
            all_spans.append(spans)
            for s, e in spans:
                for tok in re.findall(r"[^\W\d_]{3,}", para[s:e]):
                    if tok[0].isupper() and tok.lower() not in _NO_PROPAGATE:
                        names.add(tok)
        name_rx = None
        if names:
            alts = sorted({re.escape(n) for n in names}, key=len, reverse=True)
            name_rx = re.compile(r"(?<!\w)(?:" + "|".join(alts) + r")(?!\w)", re.I)

        out = []
        for para, spans in zip(paras, all_spans):
            marks = [False] * len(para)
            for s, e in spans:
                for i in range(s, e):
                    marks[i] = True
            if name_rx:
                for m in name_rx.finditer(para):
                    if para[m.start()].isupper():  # tylko wystąpienia pisane wielką literą
                        for i in range(m.start(), m.end()):
                            marks[i] = True
            res, i = [], 0
            while i < len(para):
                if marks[i]:
                    while i < len(para) and marks[i]:
                        i += 1
                    res.append(MASK)
                else:
                    res.append(para[i])
                    i += 1
            p = "".join(res)
            p = re.sub(r"\[OSOBA\](?:[ \t]+\[OSOBA\])+", MASK, p)
            out.append(p)
        return PESEL_RE.sub("[PESEL]", "\n".join(out))
