from __future__ import annotations

import os
from contextlib import ExitStack
from pathlib import Path

import psycopg
import pytest

from rag.config import load_env
from rag.db import scratch_db
from rag.embed import HashEmbedder
from rag.ingest import ingest_corpus, load_corpus
from rag.search import Retriever

FIXTURES = Path(__file__).resolve().parent / "fixtures"

load_env()


@pytest.fixture(scope="session")
def database_url() -> str:
    """TEST_DATABASE_URL, else DATABASE_URL. Each test works in its own throwaway
    schema, so existing tables in that database are never touched."""
    url = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("no TEST_DATABASE_URL / DATABASE_URL set (see .env.example)")
    try:
        psycopg.connect(url, connect_timeout=3).close()
    except psycopg.OperationalError as e:
        pytest.skip(f"PostgreSQL not reachable: {e}".splitlines()[0])
    return url


@pytest.fixture
def new_db(database_url):
    """Factory: each call returns a connection to a new empty schema (dropped after the test)."""
    with ExitStack() as stack:
        yield lambda: stack.enter_context(scratch_db(database_url))


@pytest.fixture(scope="session")
def corpus():
    return load_corpus(FIXTURES / "fake_corpus.json")


@pytest.fixture(scope="session")
def embedder():
    return HashEmbedder()


@pytest.fixture
def conn(new_db, corpus, embedder):
    c = new_db()
    ingest_corpus(c, corpus, embedder)
    return c


@pytest.fixture
def retriever(conn, embedder):
    return Retriever(conn, embedder)
