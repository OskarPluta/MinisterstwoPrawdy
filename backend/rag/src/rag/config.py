"""Environment configuration. Values come from the process environment, optionally
filled from a `.env` file (see `.env.example`); variables already set win."""

from __future__ import annotations

import os

from dotenv import find_dotenv, load_dotenv


def load_env() -> None:
    """Load `.env` from the working directory or a parent. Call from entry points only."""
    load_dotenv(find_dotenv(usecwd=True), override=False)


def database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit(
            "DATABASE_URL is not set. Copy .env.example to .env and set it, e.g. "
            "postgresql://prawdowid:prawdowid@localhost:5432/prawdowid"
        )
    return url
