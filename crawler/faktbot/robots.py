"""Parser robots.txt zgodny z RFC 9309 (grupy, *, $, najdłuższe dopasowanie, Allow wygrywa remis)
+ obsługa Crawl-delay (rozszerzenie spoza RFC, ale respektujemy)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit


@dataclass
class RobotsRules:
    rules: list[tuple[bool, str, re.Pattern]] = field(default_factory=list)  # (allow, pattern, regex)
    crawl_delay: float | None = None
    allow_all: bool = False
    disallow_all: bool = False

    def allowed(self, url: str) -> bool:
        if self.disallow_all:
            return False
        if self.allow_all:
            return True
        parts = urlsplit(url)
        path = parts.path or "/"
        if path == "/robots.txt":
            return True
        target = path + (("?" + parts.query) if parts.query else "")
        best_len, best_allow = -1, True
        for allow, pat, rx in self.rules:
            if rx.match(target):
                ln = len(pat)
                if ln > best_len or (ln == best_len and allow):
                    best_len, best_allow = ln, allow
        return best_allow


def _compile(pattern: str) -> re.Pattern:
    anchored = pattern.endswith("$")
    if anchored:
        pattern = pattern[:-1]
    rx = "".join(".*" if ch == "*" else re.escape(ch) for ch in pattern)
    return re.compile(rx + ("$" if anchored else ""))


def parse_robots(text: str, product_token: str) -> RobotsRules:
    token = product_token.lower()
    groups: list[dict] = []
    cur: dict | None = None
    prev_was_ua = False
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        key, val = line.split(":", 1)
        key, val = key.strip().lower(), val.strip()
        if key == "user-agent":
            if cur is None or not prev_was_ua:
                cur = {"agents": [], "rules": [], "delay": None}
                groups.append(cur)
            cur["agents"].append(val.lower())
            prev_was_ua = True
            continue
        prev_was_ua = False
        if cur is None:
            continue
        if key in ("allow", "disallow"):
            if not val:          # "Disallow:" (puste) = brak ograniczenia
                continue
            if not val.startswith("/") and not val.startswith("*"):
                val = "/" + val
            cur["rules"].append((key == "allow", val, _compile(val)))
        elif key == "crawl-delay":
            try:
                cur["delay"] = float(val)
            except ValueError:
                pass

    specific = [g for g in groups if any(a == token or (a != "*" and token.startswith(a)) for a in g["agents"])]
    chosen = specific or [g for g in groups if "*" in g["agents"]]
    if not chosen:
        return RobotsRules(allow_all=True)
    rules = [r for g in chosen for r in g["rules"]]
    delays = [g["delay"] for g in chosen if g["delay"] is not None]
    return RobotsRules(rules=rules, crawl_delay=max(delays) if delays else None)
