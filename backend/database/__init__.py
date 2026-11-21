"""SQL lives in .sql files in this package; Python code refers to statements by name.

    schema.sql           DDL, run on every connect (idempotent)
    queries/<group>.sql  named statements, each introduced by a `-- name: <name>` line

`query("evidence.insert")` returns the text of `-- name: insert` in queries/evidence.sql.
Statements take values only through psycopg placeholders (`%(name)s`), never through
string formatting, so no input ever becomes part of the SQL text.
"""

from __future__ import annotations

import re
from functools import cache
from importlib.resources import files
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from typing_extensions import LiteralString  # typing has it only from 3.11

_NAME = re.compile(r"^--\s*name:\s*(\w+)\s*$", re.MULTILINE)
_COMMENT = re.compile(r"^\s*--.*$\n?", re.MULTILINE)


def _read(relpath: str) -> str:
    return files(__name__).joinpath(relpath).read_text(encoding="utf-8")


def _strip_comments(sql: str) -> str:
    # Whole-line comments only; a stray "%" in a comment would otherwise be read as a placeholder.
    return _COMMENT.sub("", sql).strip()


# The SQL text comes from files shipped in this package, never from input, so it is
# declared LiteralString (what psycopg requires for query text).


@cache
def schema() -> LiteralString:
    return cast("LiteralString", _strip_comments(_read("schema.sql")))


@cache
def _group(group: str) -> dict[str, LiteralString]:
    parts = _NAME.split(_read(f"queries/{group}.sql"))
    out: dict[str, LiteralString] = {}
    for name, body in zip(parts[1::2], parts[2::2]):
        if name in out:
            raise ValueError(f"duplicate query name {group}.{name}")
        out[name] = cast("LiteralString", _strip_comments(body))
    return out


def query(name: str) -> LiteralString:
    """SQL text for "<group>.<name>"; raises KeyError for an unknown name."""
    group, _, stmt = name.partition(".")
    try:
        return _group(group)[stmt]
    except (FileNotFoundError, KeyError):
        raise KeyError(f"unknown SQL query {name!r}") from None
