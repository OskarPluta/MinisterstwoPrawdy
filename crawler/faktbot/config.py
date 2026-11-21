"""Wczytywanie sources.yaml + wartości domyślne."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import __version__

ADAPTER_TYPES = {"rss", "html_list", "sitemap", "json_api", "static_page"}

SETTINGS_DEFAULTS: dict[str, Any] = {
    "contact": None,                 # mail kontaktowy do User-Agenta (albo CRAWLER_CONTACT)
    "data_dir": "data",
    "min_delay_seconds": 3.0,        # min. odstęp między zapytaniami do jednego hosta
    "render_timeout_seconds": 20,    # limit czekania na networkidle
    "http_timeout_seconds": 30,
    "spacy_models": ["pl_core_news_md", "pl_core_news_sm"],
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
    "mask_pii": False,
    "silence_alert_hours": None,
    "doc_type": "komunikat",
    "region": None,
}

# Na tych hostach (tylko testy lokalne) wolno zejść poniżej 3 s między zapytaniami.
LOCAL_HOSTS = {"localhost", "::1"} | {f"127.0.0.{i}" for i in range(1, 10)}
HARD_MIN_DELAY = 3.0

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ConfigError(Exception):
    pass


@dataclass
class Config:
    settings: dict[str, Any]
    sources: list[dict[str, Any]]
    path: Path
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
        c = self.contact
        if not c:
            raise ConfigError(
                "Brak maila kontaktowego: ustaw zmienną CRAWLER_CONTACT albo settings.contact "
                "w sources.yaml. Bez niego crawler nie startuje."
            )
        if not EMAIL_RE.match(c):
            raise ConfigError(f"CRAWLER_CONTACT nie wygląda na adres e-mail: {c!r}")
        return f"FaktBot/{__version__} (+{c})"


def load_config(path: str | Path, data_dir: str | None = None) -> Config:
    path = Path(path).resolve()
    if not path.exists():
        raise ConfigError(f"Nie znaleziono pliku konfiguracji {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    settings = {**SETTINGS_DEFAULTS, **(raw.get("settings") or {})}
    if data_dir:
        settings["data_dir"] = data_dir
    defaults = {**SOURCE_DEFAULTS, **(raw.get("defaults") or {})}

    sources: list[dict[str, Any]] = []
    seen: set[str] = set()
    for i, s in enumerate(raw.get("sources") or []):
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
        if src["type"] == "json_api" and src.get("endpoint"):
            urls = [src["endpoint"]]
        if not urls:
            raise ConfigError(f"{name}: brak 'url' / 'urls'")
        src["urls"] = list(urls)
        if not src.get("institution"):
            raise ConfigError(f"{name}: brak pola 'institution'")
        sources.append(src)
    return Config(settings=settings, sources=sources, path=path)
