"""A ``LIKE`` on SQLite that folds the case of every script, not only ASCII (#505).

Django compiles ``icontains``, ``iexact``, ``istartswith`` and ``iendswith`` on SQLite to
``LIKE ... ESCAPE '\'``, and SQLite's own ``LIKE`` folds case for the letters A-Z alone: a
search for *école* misses *École*, and *αθηναϊκή* misses *Αθηναϊκή*. PostgreSQL's ``UPPER``
follows the database's locale and does not have the problem, so the two supported databases
disagreed. SQLite lets a connection replace ``like`` with its own function, which is what
every new connection gets here; one function covers every lookup that compiles to ``LIKE``.

**Plain text takes a shorter way** (#1134). SQLite calls the function once for every row and
every searched column, and the regular expression that folds every script costs the better
part of a microsecond a call, whatever the text is. Most text is ASCII, where folding case
is turning A-Z into a-z, and the four lookups ask one of four plain questions: holds it,
starts with it, ends with it, is it. Those are answered with `str` methods. Anything else
-- a value or a pattern with a letter outside ASCII, a ``_``, a ``%`` in the middle, a
number or a blob -- is answered by the regular expression, as everything was.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from functools import lru_cache

from django.db.backends.signals import connection_created

#: The four questions a pattern without ``_`` and without a ``%`` in the middle asks.
_IS, _STARTS, _ENDS, _HOLDS = range(4)


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


def _plain(pattern: str, escape: str | None) -> tuple[int, str] | None:
    """The question an ASCII pattern asks and the text it asks about, lower-cased.

    ``None`` when it is not one of the four: the pattern has a letter outside ASCII, a
    ``_``, or a ``%`` that is neither first nor last. Read with the escape rule of
    `_compile`, character for character, so that an escaped ``%`` or ``_`` is text here as
    it is there.
    """
    if not pattern.isascii():
        return None
    # Each piece is one character of text, or `None` where a `%` stood.
    pieces: list[str | None] = []
    characters = iter(pattern)
    for character in characters:
        if escape and character == escape:
            pieces.append(next(characters, escape))
        elif character == "%":
            pieces.append(None)
        elif character == "_":
            return None
        else:
            pieces.append(character)
    start, end = 0, len(pieces)
    while start < end and pieces[start] is None:
        start += 1
    while end > start and pieces[end - 1] is None:
        end -= 1
    text = pieces[start:end]
    if None in text:
        return None
    before, after = start > 0, end < len(pieces)
    if before:
        question = _HOLDS if after else _ENDS
    else:
        question = _STARTS if after else _IS
    return question, "".join(text).lower()


@lru_cache(maxsize=512)
def _read(pattern: str, escape: str | None) -> tuple[int | None, str, Callable]:
    """Both ways to answer a pattern: the plain question, if it asks one, and the expression.

    Kept together so that a call looks one thing up, whichever way it then goes. Kept per
    process and never emptied, as the compiled expressions are: each entry is worked out
    from the pattern and the escape character alone, so nothing can make one wrong.
    """
    question, literal = _plain(pattern, escape) or (None, "")
    return question, literal, _compile(pattern, escape).fullmatch


def like_by_expression(pattern, value, escape=None):
    """``value LIKE pattern ESCAPE escape`` by regular expression alone: the measure.

    The whole of `like` until #1134, word for word. `like` does not call it, since a call
    more on every row is the kind of cost the change is there to save; it stays as what the
    tests hold `like` to, answer for answer.
    """
    if pattern is None or value is None:
        return None
    return 1 if _compile(str(pattern), escape or None).fullmatch(str(value)) else 0


def like(pattern, value, escape=None):
    """``value LIKE pattern ESCAPE escape``, as SQLite's argument order has it."""
    if pattern is None or value is None:
        return None
    question, literal, fullmatch = _read(str(pattern), escape or None)
    # `isascii()` reads a flag the string carries; it does not look at the text.
    if question is not None and type(value) is str and value.isascii():
        lowered = value.lower()
        if question == _HOLDS:
            return 1 if literal in lowered else 0
        if question == _IS:
            return 1 if literal == lowered else 0
        if question == _STARTS:
            return 1 if lowered.startswith(literal) else 0
        return 1 if lowered.endswith(literal) else 0
    return 1 if fullmatch(str(value)) else 0


def _like_without_escape(pattern, value):
    return like(pattern, value, None)


def install(sender, connection, **kwargs) -> None:
    if connection.vendor != "sqlite":
        return
    connection.connection.create_function("like", 3, like, deterministic=True)
    connection.connection.create_function("like", 2, _like_without_escape, deterministic=True)


def connect() -> None:
    connection_created.connect(install, dispatch_uid="postulo-unicode-like")
