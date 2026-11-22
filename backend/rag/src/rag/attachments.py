"""Material the user forwarded (web page, screenshot, PDF, video metadata), already turned into text.

An attachment is what gets checked, never evidence. The claim-extraction model sees it as escaped
data in its own <attachment> block, its claims go through the same search and verdict as typed
text, and nothing from it is ever cited or linked. The `intake` package produces attachments;
rag only consumes them, so rag does not depend on intake.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from .timeutil import fmt_local, parse_ts

MAX_ATTACHMENTS = 4  # links found in the text + uploaded files
MAX_ATTACHMENT_CHARS = 6_000  # per attachment, what the extraction model sees
LOG_EXCERPT_CHARS = 500  # per attachment, what the check log keeps (masked)
OLD_MATERIAL_AFTER = timedelta(days=30)

KIND_PL = {"url": "strona internetowa", "youtube": "film w serwisie YouTube", "image": "obraz", "pdf": "dokument PDF"}
DATE_KIND_PL = {
    "published": "data publikacji",
    "taken": "data wykonania zdjęcia (EXIF)",
    "created": "data utworzenia pliku",
}
NOTE_INSTRUCTIONS = "Materiał zawierał polecenia skierowane do systemów AI. Zignorowaliśmy je."


@dataclass
class Attachment:
    kind: str  # url | youtube | image | pdf
    label: str  # file name or the address the user sent; shown as plain text, never as a link
    text: str = ""  # extracted text; "" when nothing could be read
    method: str = ""  # how the text was obtained, e.g. html, youtube_oembed, ocr_llm, pdf_text
    material_date: str | None = None  # ISO 8601 (UTC): when the material itself was made/published
    material_date_kind: str | None = None  # a DATE_KIND_PL key
    notes: list[str] = field(default_factory=list)  # neutral context: dates, metadata, domain status
    limitations: list[str] = field(default_factory=list)  # what could not be read or checked
    meta: dict[str, Any] = field(default_factory=dict)  # mime, size, sha256…; internal, not shown
    id: str = ""  # A1, A2…; assigned by the Verifier

    @property
    def readable(self) -> bool:
        return bool(self.text.strip())


@dataclass
class InputSummary:
    """What the user sees about each attachment: what was read, how, and what could not be done."""

    id: str
    kind: str
    kind_pl: str
    label: str
    method: str
    readable: bool
    date_label: str | None  # "12.03.2019 (data publikacji)"
    notes: list[str]
    limitations: list[str]
    text: str  # the text the model saw, so the person can spot reading errors
    meta: dict[str, Any] = field(default_factory=dict)  # internal, stripped by the API


def prepare(attachments: list[Attachment]) -> list[Attachment]:
    """Assign ids, cap the count and the length the model sees (with a stated limitation)."""
    out: list[Attachment] = []
    for i, a in enumerate(attachments[:MAX_ATTACHMENTS], 1):
        a.id = f"A{i}"
        if len(a.text) > MAX_ATTACHMENT_CHARS:
            a.text = a.text[:MAX_ATTACHMENT_CHARS].rsplit(" ", 1)[0] + " …"
            a.limitations.append(
                f"Materiał jest długi. Sprawdzamy tylko pierwsze {MAX_ATTACHMENT_CHARS:,} znaków.".replace(",", " ")
            )
        out.append(a)
    return out


def date_label(a: Attachment) -> str | None:
    if not a.material_date:
        return None
    kind = DATE_KIND_PL.get(a.material_date_kind or "", "data materiału")
    return f"{fmt_local(a.material_date, with_time=False)} ({kind})"


def summary(a: Attachment) -> InputSummary:
    return InputSummary(
        id=a.id, kind=a.kind, kind_pl=KIND_PL.get(a.kind, a.kind), label=a.label, method=a.method,
        readable=a.readable, date_label=date_label(a), notes=list(dict.fromkeys(a.notes)),
        limitations=list(dict.fromkeys(a.limitations)), text=a.text, meta=dict(a.meta),
    )


def old_material_note(a: Attachment, now: datetime) -> str | None:
    """Scenario B: true material reused out of its time. A note, never a verdict."""
    dt = parse_ts(a.material_date)
    if dt is None or now - dt <= OLD_MATERIAL_AFTER:
        return None
    return (
        f"Materiał, z którego pochodzi to twierdzenie ({KIND_PL.get(a.kind, a.kind)}), ma datę "
        f"{fmt_local(a.material_date, with_time=False)} ({DATE_KIND_PL.get(a.material_date_kind or '', 'data materiału')}). "
        "Jeśli ktoś udostępnia go jako bieżący, może być wyrwany z kontekstu."
    )
