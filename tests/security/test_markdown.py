"""Markdown in a listing's description under hostile and enormous input (#665)."""

import re
import time

import pytest

from postulo.core import markdown as md

#: 40,000 characters is what a capture is held to; a form takes more, so the worst cases are
#: also run at four times that.
SIZE = 40_000
#: Generous for a loaded CI machine: a pass over 40,000 characters takes about a second on a
#: quiet one and the time grows with the length, where a quadratic parse of this input
#: takes minutes. The bound is for that size, and four times it for the longer input.
BOUND_SECONDS = 15

WORST_CASES = {
    "strong runs": "**x**" * (SIZE // 5),
    "unclosed strong": "**x" * (SIZE // 3),
    "unclosed emphasis": "*_" * (SIZE // 2),
    "open brackets": "[" * SIZE,
    "open links": "[a](" * (SIZE // 4),
    "reference brackets": "[[[[a]]]][x]" * (SIZE // 12),
    "block quotes": ">" * SIZE,
    "quoted lines": "> " * (SIZE // 2),
    "nested lists": "".join("  " * i + "- x\n" for i in range(200)) * 5,
    "deep list marker run": "- " * (SIZE // 2),
    "backticks": "`" * SIZE,
    "alternating backticks": "`a" * (SIZE // 2),
    "long link title": '[x](https://example.com "' + "t" * SIZE + '")',
    "long link address": "[x](https://example.com/" + "a" * SIZE + ")",
    "angle brackets": "<" * SIZE,
    "autolink openers": "<https://" * (SIZE // 9),
    "headings": "# h\n" * (SIZE // 4),
    "entities": "&amp;" * (SIZE // 5),
    "escapes": "\\*" * (SIZE // 2),
}


#: Four times the size is `heavy`: run beside the rest of the suite, in a worker that has
#: grown through thousands of tests, it pushed CI's 2 GB job container over and the kernel
#: killed the worker (2026-10-10, three runs). Same cases, a process of their own.
@pytest.mark.parametrize("name", WORST_CASES)
@pytest.mark.parametrize("factor", [1, pytest.param(4, marks=pytest.mark.heavy)])
def test_the_worst_cases_finish_in_bounded_time_and_still_pass_the_allowlist(name, factor):
    source = WORST_CASES[name] * factor
    started = time.monotonic()
    html = str(md.render(source))
    words = md.plain(source)
    assert time.monotonic() - started < BOUND_SECONDS * factor, name
    assert isinstance(words, str)
    # What came out is what the allowlist would let through: cleaning it again is a no-op.
    assert md.sanitize(html) == md.sanitize(md.sanitize(html))
    assert set(re.findall(r"\s([a-z-]+)=", html)) <= {"href", "rel", "target", "start"}
    assert set(re.findall(r"</?([a-zA-Z][a-zA-Z0-9]*)", html)) <= md.ALLOWED_TAGS


def test_nesting_is_cut_short_rather_than_followed():
    source = "> " * 500 + "x"
    html = str(md.render(source))
    assert html.count("<blockquote>") <= 100
    assert md.sanitize(html) == html
