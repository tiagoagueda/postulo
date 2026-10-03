"""A ``LIKE`` on SQLite that folds the case of every script, not only ASCII (#505).

Django compiles ``icontains``, ``iexact``, ``istartswith`` and ``iendswith`` on SQLite to
``LIKE ... ESCAPE '\'``, and SQLite's own ``LIKE`` folds case for the letters A-Z alone: a
search for *école* misses *École*, and *αθηναϊκή* misses *Αθηναϊκή*. PostgreSQL's ``UPPER``
follows the database's locale and does not have the problem, so the two supported databases
disagreed. SQLite lets a connection replace ``like`` with its own function, which is what
every new connection gets here; one function covers every lookup that compiles to ``LIKE``.
"""

from __future__ import annotations

import re
from functools import lru_cache

from django.db.backends.signals import connection_created


@lru_cache(maxsize=512)
def _compile(pattern: str, escape: str | None) -> re.Pattern:
    """SQL ``LIKE`` syntax as a regular expression: ``%`` any run, ``_`` any one character."""
    parts: list[str] = []
    characters = iter(pattern)
    for character in characters:
        if escape and character == escape:
            parts.append(re.escape(next(characters, escape)))
        elif character == "%":
            parts.append(".*")
        elif character == "_":
            parts.append(".")
        else:
            parts.append(re.escape(character))
    return re.compile("".join(parts), re.IGNORECASE | re.DOTALL)


def like(pattern, value, escape=None):
    """``value LIKE pattern ESCAPE escape``, as SQLite's argument order has it."""
    if pattern is None or value is None:
        return None
    return 1 if _compile(str(pattern), escape or None).fullmatch(str(value)) else 0


def _like_without_escape(pattern, value):
    return like(pattern, value, None)


def install(sender, connection, **kwargs) -> None:
    if connection.vendor != "sqlite":
        return
    connection.connection.create_function("like", 3, like, deterministic=True)
    connection.connection.create_function("like", 2, _like_without_escape, deterministic=True)


def connect() -> None:
    connection_created.connect(install, dispatch_uid="postulo-unicode-like")
