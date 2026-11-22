"""Wczytywanie sources.yaml + wartości domyślne."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from . import __version__
from .removals import REMOVALS_FILE, Removals, load_removals

# backend/crawler/sources.yaml (pakiet instalowany w trybie edytowalnym przez workspace uv)
DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "sources.yaml"

ADAPTER_TYPES = {"rss", "html_list", "sitemap", "json_api", "static_page", "mcp_tool"}

SETTINGS_DEFAULTS: dict[str, Any] = {
    "contact": None,                 # mail kontaktowy do User-Agenta (albo CRAWLER_CONTACT)
    "data_dir": "data",
    "min_delay_seconds": 3.0,        # min. odstęp między zapytaniami do jednego hosta
    "render_timeout_seconds": 20,    # limit czekania na networkidle
    "http_timeout_seconds": 30,
    "default_backoff_minutes": 15,   # 429/503 bez Retry-After
    "forbidden_backoff_minutes": 60,  # 403
    "robots_ttl_hours": 24,
    "robots_unavailable_retry_minutes": 30,
}

SOURCE_DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "interval_minutes": 60,
    "max_items_per_run": 30,
    "recheck_hours": 24,
    "fetch_full": True,
    "silence_alert_hours": None,
    "doc_type": "komunikat",
    "region": None,
}

# Na tych hostach (tylko testy lokalne) wolno zejść poniżej 3 s między zapytaniami.
LOCAL_HOSTS = {"localhost", "::1"} | {f"127.0.0.{i}" for i in range(1, 10)}
HARD_MIN_DELAY = 3.0

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ConfigError(Exception):
    """Błędna konfiguracja albo brak warunków startu (np. maila kontaktowego)."""


@dataclass
class Config:
    """Wczytane ustawienia i źródła (z domyślnymi wartościami)."""
    settings: dict[str, Any]
    sources: list[dict[str, Any]]
    path: Path
    removals: Removals = field(default_factory=Removals)
    data_dir: Path = field(init=False)

    def __post_init__(self) -> None:
        d = Path(self.settings["data_dir"])
        self.data_dir = d if d.is_absolute() else (self.path.parent / d)

    def source(self, name: str) -> dict[str, Any]:
        for s in self.sources:
            if s["name"] == name:
                return s
        raise ConfigError(f"Nie ma źródła o nazwie {name!r}")

    @property
    def contact(self) -> str | None:
        c = os.environ.get("CRAWLER_CONTACT") or self.settings.get("contact")
        return c.strip() if c else None

    def user_agent(self) -> str:
        """Uczciwy User-Agent `FaktBot/<wersja> (+kontakt)`; bez poprawnego maila – ConfigError."""
        c = self.contact
        if not c:
            raise ConfigError(
                "Brak maila kontaktowego: ustaw zmienną CRAWLER_CONTACT albo settings.contact "
                "w sources.yaml. Bez niego crawler nie startuje."
            )
        if not EMAIL_RE.match(c):
            raise ConfigError(f"CRAWLER_CONTACT nie wygląda na adres e-mail: {c!r}")
        return f"FaktBot/{__version__} (+{c})"


LICENSE_FIELDS = ("license", "terms_url", "processed_notice")


def _host_matches(host: str, patterns: list[str]) -> bool:
    return any(host == p or host.endswith("." + p) for p in patterns)


def _apply_license(src: dict[str, Any], rules: list[dict[str, Any]]) -> None:
    """Uzupełnia license / terms_url / processed_notice z pierwszej reguły `licenses`, której
    `hosts` pasuje do hosta źródła. Pola wpisane w samym źródle mają pierwszeństwo."""
    host = (urlparse(src["urls"][0]).hostname or "").lower()
    for rule in rules:
        if _host_matches(host, [h.lower() for h in rule.get("hosts") or []]):
            for k in LICENSE_FIELDS:
                if rule.get(k) and not src.get(k):
                    src[k] = rule[k]
            break
    if not src.get("license"):
        raise ConfigError(
            f"{src['name']}: brak licencji ani podstawy prawnej (pole 'license' albo reguła w 'licenses' "
            f"dla hosta {host}). Bez niej treści nie wolno archiwizować: docs/DANE_I_LICENCJE.md"
        )


def license_for_host(host: str, path: str | Path = DEFAULT_CONFIG) -> dict[str, Any]:
    """Pola licencji z reguły `licenses` dla hosta (np. dla wyszukiwania na żywo w rag/live.py,
    które nie ma własnych wpisów w sources.yaml). Brak reguły: wartości None."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    for rule in raw.get("licenses") or []:
        if _host_matches(host.lower(), [h.lower() for h in rule.get("hosts") or []]):
            return {k: rule.get(k) for k in LICENSE_FIELDS}
    return dict.fromkeys(LICENSE_FIELDS)


def load_config(path: str | Path = DEFAULT_CONFIG, data_dir: str | None = None) -> Config:
    """Wczytuje i waliduje sources.yaml (i removed.yaml obok); `data_dir` nadpisuje settings.data_dir."""
    path = Path(path).resolve()
    if not path.exists():
        raise ConfigError(f"Nie znaleziono pliku konfiguracji {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    settings = {**SETTINGS_DEFAULTS, **(raw.get("settings") or {})}
    if data_dir:
        settings["data_dir"] = data_dir
    defaults = {**SOURCE_DEFAULTS, **(raw.get("defaults") or {})}

    # Dodatkowe pliki źródeł: <katalog sources.yaml>/sources.d/*.yaml (kolejność alfabetyczna).
    # Każdy plik ma własną listę `sources:` (kotwice YAML, np. profil gov.pl, działają tylko w obrębie pliku).
    raw_sources = list(raw.get("sources") or [])
    extra_dir = path.parent / "sources.d"
    if extra_dir.is_dir():
        for extra in sorted(extra_dir.glob("*.yaml")):
            part = yaml.safe_load(extra.read_text(encoding="utf-8")) or {}
            raw_sources.extend(part.get("sources") or [])

    sources: list[dict[str, Any]] = []
    seen: set[str] = set()
    for i, s in enumerate(raw_sources):
        if not isinstance(s, dict) or "name" not in s:
            raise ConfigError(f"Źródło #{i} nie ma pola 'name'")
        src = {**defaults, **s}
        name = src["name"]
        if name in seen:
            raise ConfigError(f"Zduplikowana nazwa źródła: {name}")
        seen.add(name)
        if src.get("type") not in ADAPTER_TYPES:
            raise ConfigError(f"{name}: nieznany typ {src.get('type')!r} (dozwolone: {sorted(ADAPTER_TYPES)})")
        urls = src.get("urls") or ([src["url"]] if src.get("url") else [])
        if src["type"] in ("json_api", "mcp_tool") and src.get("endpoint"):
            urls = [src["endpoint"]]
        if not urls:
            raise ConfigError(f"{name}: brak 'url' / 'urls'")
        src["urls"] = list(urls)
        if not src.get("institution"):
            raise ConfigError(f"{name}: brak pola 'institution'")
        _apply_license(src, list(raw.get("licenses") or []))
        sources.append(src)
    try:
        removals = load_removals(path.parent / REMOVALS_FILE)
    except ValueError as e:
        raise ConfigError(str(e)) from None
    return Config(settings=settings, sources=sources, path=path, removals=removals)
