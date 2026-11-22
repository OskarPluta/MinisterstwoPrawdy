"""Format CheckResult and source catalog into Discord embeds.

Preserves Prawdowid presentation invariants:
- Model-written text (`claim`, `explanation`, `message`) is escaped and stripped of URLs
  via `rag.verify._model_text`.
- Links come exclusively from `citation.url` (verified http/https URLs from the database).
- Structured records (`is_verbatim=False`) are rendered as `Dane ze źródła: ...`.
- The source credit (`Citation.attribution`, e.g. IMGW terms) stays under the citation header.
- `insufficient` ("brak wystarczających dowodów") and `unverifiable` are never red.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import discord

from rag.timeutil import WARSAW, parse_ts

from rag.verify import (
    ROLE_PL,
    CheckResult,
    Citation,
    ClaimResult,
    Strength,
    _md,
    _model_text,
    _safe_url,
)

# Ministerstwo Prawdy palette (frontend/src/design/tokens.css)
COLOR_SUCCESS = 0x00695C
COLOR_DANGER = 0xB3261E
COLOR_WARNING = 0x8A4F00
COLOR_INFO = 0x0A4D9E

DISCORD_TITLE_MAX = 256
DISCORD_DESC_MAX = 4096
DISCORD_TOTAL_EMBEDS_MAX = 6000
DISCORD_CONTENT_MAX = 2000

STATUS_NOTICE: dict[str, tuple[int, str]] = {
    "needs_clarification": (COLOR_WARNING, "Doprecyzuj pytanie"),
    "too_long": (COLOR_WARNING, "Uwaga"),
    "out_of_scope": (COLOR_INFO, "Informacja"),
    "error": (COLOR_DANGER, "Błąd"),
}

# `insufficient` is never red: no evidence does not mean false.
VERDICT_VIEW: dict[str, tuple[int, str]] = {
    "true": (COLOR_SUCCESS, "Źródła potwierdzają to twierdzenie"),
    "false": (COLOR_DANGER, "Źródła przeczą temu twierdzeniu"),
    "misleading": (COLOR_WARNING, "To twierdzenie wprowadza w błąd"),
    "insufficient": (COLOR_INFO, "Nie znaleźliśmy wystarczających dowodów"),
    "unverifiable": (COLOR_INFO, "To opinia, a nie fakt do sprawdzenia"),
    "error": (COLOR_WARNING, "Nie udało się sprawdzić tego twierdzenia"),
}

KNOWN_NARRATIVE_OPINION = (COLOR_WARNING, "Ta opinia powtarza znaną narrację dezinformacyjną")
KNOWN_NARRATIVE_LABEL = "Znana narracja dezinformacyjna"
STRENGTH_LABEL = "Siła dowodów"
CONFLICT_MSG = "Źródła podają różne informacje. Szczegóły znajdziesz w cytatach poniżej."
INJECTION_MSG = (
    "Twoja wiadomość zawierała polecenia skierowane do systemu. "
    "Zignorowaliśmy je i sprawdziliśmy tylko twierdzenia."
)

# Share and delete of the check log (same keys as the web channel, backend/api/src/api/app.py).
# check_id (UUID4) deletes the check, so it is only ever sent privately to the requester.
CHECK_ID_RX = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
SHARE_PATH_RX = re.compile(r"^/wynik/[A-Za-z0-9_-]{16}$")

MSG_BAD_ID = "To nie jest prawidłowy identyfikator sprawdzenia. Skopiuj go z prywatnej wiadomości bota."
MSG_SHARE_OFF = "Udostępnianie wyników jest wyłączone na tym serwerze (bot nie zna adresu strony z wynikami)."
MSG_DELETED = "Usunęliśmy zapis tego sprawdzenia. Link do wyniku, jeśli był, przestał działać."
MSG_ALREADY_DELETED = "Zapis tego sprawdzenia został już usunięty."
MSG_UNSHARED = "Wyłączyliśmy udostępnianie. Link do wyniku przestał działać."
MSG_NOT_REQUESTER = "Tego przycisku może użyć tylko osoba, która zleciła sprawdzenie."
MSG_CONFIRM_DELETE = "Na pewno usunąć zapis tego sprawdzenia? Tego nie da się cofnąć."
# Same wording as the API's 404 responses.
MSG_NOT_FOUND_SHARE = "Nie ma zapisu tego sprawdzenia, więc nie można go udostępnić."
MSG_NOT_FOUND_UNSHARE = "Nie ma zapisu tego sprawdzenia."
MSG_NOT_FOUND_DELETE = "Nie ma zapisu o tym identyfikatorze (mógł już zostać usunięty)."


@dataclass(frozen=True)
class VerdictEmbed:
    """Discord message payload produced from a CheckResult."""

    content: str | None
    embeds: list[discord.Embed]


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    if limit <= 1:
        return "…"[:limit]
    cut = text[: limit - 1].rsplit(" ", 1)[0]
    if len(cut) < limit // 2:
        cut = text[: limit - 1]
    return cut.rstrip() + "…"


def _strength_from_dict(raw: Any) -> Strength | None:
    if not isinstance(raw, Mapping):
        return None
    return Strength(
        level=str(raw.get("level", "")),
        level_pl=str(raw.get("level_pl", "")),
        reasons=[str(r) for r in raw.get("reasons", ())],
        confidence=float(raw["confidence"]) if isinstance(raw.get("confidence"), (int, float)) else None,
    )


def check_result_from_dict(data: Mapping[str, Any]) -> CheckResult:
    """Reconstruct a CheckResult from a /api/verify JSON payload or CheckResult.to_dict()."""
    claims: list[ClaimResult] = []
    for raw_c in data.get("claims", ()):
        citations = [
            Citation(
                n=int(cit["n"]),
                passage_id=str(cit.get("passage_id", "")),
                role=str(cit["role"]),
                title=str(cit["title"]),
                publisher=str(cit["publisher"]),
                url=_safe_url(cit.get("url")),
                date_label=str(cit["date_label"]),
                text=str(cit["text"]),
                is_verbatim=bool(cit["is_verbatim"]),
                attribution=str(cit["attribution"]) if cit.get("attribution") else None,
            )
            for cit in raw_c.get("citations", ())
        ]
        claims.append(
            ClaimResult(
                claim=str(raw_c["claim"]),
                verdict=str(raw_c["verdict"]),
                verdict_pl=str(raw_c["verdict_pl"]),
                explanation=str(raw_c["explanation"]),
                citations=citations,
                notes=[str(n) for n in raw_c.get("notes", ())],
                shortlist_ids=[str(x) for x in raw_c.get("shortlist_ids", ())],
                invented_ids=[str(x) for x in raw_c.get("invented_ids", ())],
                downgraded_from=raw_c.get("downgraded_from"),
                conflict=bool(raw_c.get("conflict", False)),
                known_disinformation=bool(raw_c.get("known_disinformation", False)),
                strength=_strength_from_dict(raw_c.get("strength")),
                error=raw_c.get("error"),
            )
        )
    return CheckResult(
        check_id=str(data.get("check_id", "")),
        status=str(data["status"]),
        message=data.get("message"),
        claims=claims,
        injection_detected=bool(data.get("injection_detected", False)),
        duration_ms=int(data.get("duration_ms", 0)),
        error=data.get("error"),
    )


def _claim_view(claim: ClaimResult) -> tuple[int, str]:
    if claim.known_disinformation and claim.verdict == "unverifiable":
        return KNOWN_NARRATIVE_OPINION
    return VERDICT_VIEW.get(claim.verdict, VERDICT_VIEW["error"])


def _format_citation_header(cit: Citation) -> str:
    safe_url = _safe_url(cit.url)
    title = f"[{_md(cit.title)}]({safe_url})" if safe_url else _md(cit.title)
    role_pl = ROLE_PL.get(cit.role, _md(cit.role))
    head = f"{cit.n}. {title} · {_md(cit.publisher)}, {_md(cit.date_label)} ({role_pl})"
    # Credit required by the source's terms; part of the header so it survives excerpt trimming.
    return f"{head}\n_{_md(cit.attribution)}_" if cit.attribution else head


def _format_citation_body(text: str, is_verbatim: bool) -> str:
    body = _md(text)
    if is_verbatim:
        return "> " + body.replace("\n", "\n> ")
    return f"Dane ze źródła: {body}"


def _build_claim_description(claim: ClaimResult, max_chars: int) -> str:
    parts: list[str] = [f"**Twierdzenie:** {_model_text(claim.claim)}"]
    if claim.known_disinformation:
        parts.append(f"**{KNOWN_NARRATIVE_LABEL}:** Tak, udokumentowana w bazie weryfikacji")

    parts.append("")
    parts.append(_model_text(claim.explanation))

    if claim.conflict:
        parts.append("")
        parts.append(f"_{CONFLICT_MSG}_")

    # Computed by code (assess_strength), not by the model; neutral, not a verdict colour.
    if claim.strength:
        parts.append("")
        parts.append(f"**{STRENGTH_LABEL}:** {_md(claim.strength.level_pl)}")
        parts.extend(f"• {_md(r)}" for r in claim.strength.reasons)

    if claim.notes:
        parts.append("")
        parts.extend(f"⚠ {_md(note)}" for note in claim.notes)

    base = "\n".join(parts).strip()
    if not claim.citations:
        return _truncate(base, max_chars)

    header = f"\n\n**Źródła ({len(claim.citations)}):**"
    if len(base) + len(header) >= max_chars:
        return _truncate(base, max_chars)

    lines = [base + header]
    used = len(lines[0])
    for cit in claim.citations:
        cit_head = _format_citation_header(cit)
        cit_body = _format_citation_body(cit.text, cit.is_verbatim)
        entry = f"{cit_head}\n{cit_body}"
        needed = 1 + len(entry)
        if used + needed <= max_chars:
            lines.append(entry)
            used += needed
            continue

        # Try keeping the citation link header and trimming the excerpt to fit.
        avail_for_body = max_chars - used - 1 - len(cit_head) - 1
        prefix = "> " if cit.is_verbatim else "Dane ze źródła: "
        if avail_for_body > len(prefix) + 20:
            trimmed_raw = _truncate(cit.text, avail_for_body - len(prefix))
            trimmed_entry = f"{cit_head}\n{_format_citation_body(trimmed_raw, cit.is_verbatim)}"
            if used + 1 + len(trimmed_entry) <= max_chars:
                lines.append(trimmed_entry)
                used += 1 + len(trimmed_entry)
                continue
        if used + 1 + len(cit_head) <= max_chars:
            lines.append(cit_head)
            used += 1 + len(cit_head)
        else:
            break

    return _truncate("\n".join(lines), max_chars)


def _claim_embed(claim: ClaimResult, index: int, total: int, max_desc: int) -> discord.Embed:
    color, view_title = _claim_view(claim)
    prefix = f"({index + 1}/{total}) " if total > 1 else ""
    verdict_badge = f"[{claim.verdict_pl.upper()}] "
    title = _truncate(f"{prefix}{verdict_badge}{view_title}", DISCORD_TITLE_MAX)
    description = _build_claim_description(claim, max_desc)
    embed = discord.Embed(title=title, description=description, color=color)
    embed.set_footer(text="Prawdowid · Weryfikacja w oparciu o źródła")
    return embed


def format_check_result(result: CheckResult | Mapping[str, Any]) -> VerdictEmbed:
    """Convert a CheckResult (or /api/verify dict) into Discord message content and embeds."""
    res = result if isinstance(result, CheckResult) else check_result_from_dict(result)

    notices: list[str] = []
    if res.injection_detected:
        notices.append(f"⚠ {INJECTION_MSG}")
    if res.status == "ok" and res.message:
        notices.append(_model_text(res.message))

    content = _truncate("\n".join(notices), DISCORD_CONTENT_MAX) if notices else None

    if res.status != "ok":
        color, title = STATUS_NOTICE.get(res.status, STATUS_NOTICE["error"])
        msg = _model_text(res.message or "") or "Nie udało się przetworzyć zapytania."
        embed = discord.Embed(
            title=_truncate(title, DISCORD_TITLE_MAX),
            description=_truncate(msg, DISCORD_DESC_MAX),
            color=color,
        )
        embed.set_footer(text="Prawdowid")
        return VerdictEmbed(content=content, embeds=[embed])

    if not res.claims:
        embed = discord.Embed(
            title="Informacja",
            description="Nie znaleziono twierdzeń do wyświetlenia.",
            color=COLOR_INFO,
        )
        return VerdictEmbed(content=content, embeds=[embed])

    # Discord caps total characters across all embeds in a message at 6000.
    total_claims = len(res.claims)
    per_embed_budget = min(DISCORD_DESC_MAX, max(600, (DISCORD_TOTAL_EMBEDS_MAX - 400) // total_claims - 180))
    embeds = [_claim_embed(claim, i, total_claims, per_embed_budget) for i, claim in enumerate(res.claims)]
    return VerdictEmbed(content=content, embeds=embeds)


def format_sources_embed(totals: Mapping[str, Any], sources: list[Mapping[str, Any]]) -> discord.Embed:
    """Format corpus totals and audited source list as a Discord embed."""
    records = totals.get("current_records", 0)
    domains = len({s.get("publishing_domain") for s in sources if s.get("publishing_domain")})
    header = (
        f"**Źródła w korpusie:** {len(sources)} · "
        f"**Domeny:** {domains} · "
        f"**Aktualne rekordy:** {records}"
    )
    lines = [header, ""]
    for src in sources:
        publisher = _md(str(src.get("publisher", "")))
        domain = _md(str(src.get("publishing_domain", "")))
        src_type = _md(str(src.get("source_type", "")))
        count = src.get("current_records", 0)
        line = f"• **{publisher}** (`{domain}`, {src_type}) — {count} rek."
        if sum(len(x) + 1 for x in lines) + len(line) > DISCORD_DESC_MAX - 60:
            lines.append(f"… oraz {len(sources) - (len(lines) - 2)} kolejnych źródeł.")
            break
        lines.append(line)
    embed = discord.Embed(
        title="Katalog źródeł Prawdowid",
        description="\n".join(lines),
        color=COLOR_INFO,
    )
    embed.set_footer(text="Prawdowid · Audyt źródeł")
    return embed


def parse_check_id(raw: str | None) -> str | None:
    """A check_id typed or pasted by a person, normalised; None when it is not a UUID4-shaped id."""
    value = (raw or "").strip().strip("`").strip().lower()
    return value if CHECK_ID_RX.match(value) else None


def public_base_url(raw: str | None) -> str | None:
    """The frontend's public base URL (PRAWDOWID_PUBLIC_URL), or None when unset or not http(s)."""
    value = (raw or "").strip().rstrip("/")
    if not value:
        return None
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.netloc or parts.query or parts.fragment:
        return None
    return value


def share_url(base: str | None, path: str | None) -> str | None:
    """Public read-only page of a share: <base>/wynik/<share_id>. None when either part is unusable."""
    if not base or not path or not SHARE_PATH_RX.match(path):
        return None
    return base + path


def format_share_link(url: str, expires_at: str | None) -> str:
    """'Link do wyniku (tylko do odczytu, działa do DD.MM.RRRR): <url>' (date in Europe/Warsaw)."""
    until = parse_ts(expires_at)
    if until is None:
        return f"Link do wyniku (tylko do odczytu): {url}"
    return f"Link do wyniku (tylko do odczytu, działa do {until.astimezone(WARSAW):%d.%m.%Y}): {url}"


def format_private_note(check_id: str, *, shareable: bool, sharing_enabled: bool) -> str:
    """The message only the requester sees: the deletion key and what to do with it."""
    lines = [
        f"Identyfikator Twojego sprawdzenia: `{check_id}`",
        f"Zapis usuniesz przyciskiem poniżej albo poleceniem `/usun identyfikator:{check_id}`.",
        "Nie pokazuj identyfikatora innym: kto go zna, może usunąć zapis.",
    ]
    if shareable and sharing_enabled:
        lines.append("Link do wyniku (tylko do odczytu) utworzysz przyciskiem albo poleceniem `/udostepnij`.")
    elif shareable:
        lines.append(MSG_SHARE_OFF)
    return "\n".join(lines)
