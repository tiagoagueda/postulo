"""Things about templates that are wrong on every page at once, caught before a browser is.

Django's ``{# #}`` comment is single-line only; spread over two lines it is not a comment
but text, printed on the page with its braces. A note that mentions a tag in passing then
adds that tag to the document. It happened once, and the accessibility suite found it by
the Columns menu having vanished into a stray ``<details>``. This is the cheaper check.
"""

import re
from pathlib import Path

import pytest

TEMPLATES = sorted((Path(__file__).resolve().parents[1] / "src" / "postulo").rglob("*.html"))


def multiline_hash_comments(text: str) -> list[int]:
    """Line numbers of ``{#`` whose ``#}`` is on a later line."""
    found = []
    for match in re.finditer(r"\{#", text):
        end = text.find("#}", match.end())
        chunk = text[match.end() : end if end != -1 else len(text)]
        if "\n" in chunk:
            found.append(text.count("\n", 0, match.start()) + 1)
    return found


@pytest.mark.parametrize(
    "path", TEMPLATES, ids=lambda p: str(p.relative_to(TEMPLATES[0].parents[3]))
)
def test_no_template_comment_spans_lines(path: Path):
    lines = multiline_hash_comments(path.read_text(encoding="utf-8"))
    assert not lines, f"{path.name}: {{# #}} spans lines at {lines}; use {{% comment %}} instead"


def test_the_detector_knows_the_difference():
    assert multiline_hash_comments("{# fine #}\n<p>{# also fine #}</p>") == []
    assert multiline_hash_comments("<p>\n{# not\n   fine #}\n") == [2]
