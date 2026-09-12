"""The corpus: whole pages from real boards, read end to end, against what a person expected.

`test_capture.py` covers the pieces -- one helper, one shape, one value. This covers the
thing somebody actually does, which is press the button on a page from a real board, and it
is the only test here that answers "is it accurate?" with a number rather than an opinion.

Each case is a pair in ``fixtures/postings/``: ``<name>.html`` is the page and
``<name>.json`` is every field expected out of it, with a ``why`` saying what that board
does differently. The pages are written rather than saved from the web -- a saved advert is
somebody else's copyright and it goes stale the week the posting closes -- but every quirk
in them was taken from a live page of that board and the file says which.

**The same corpus is in the browser extension**, at
``postulo-chromium/test/fixtures/``, where `parse.js` is held to the same expected values.
That is the whole point of it: a page read in somebody's browser must come out identical to
that page read here when it is sent, and two readers agreeing on six pages is the only
evidence of it there can be. A fixture added on one side belongs on the other.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from postulo.plugins.builtin import BUILTIN_SOURCES

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "postings"

#: The name each shipped source declares, which is what a fixture names.
SOURCE_NAMES = {
    "BoardSource": "board",
    "SchemaOrgSource": "schema.org",
    "PageMetadataSource": "page-metadata",
}


def read(url: str, html: str) -> tuple[str | None, object]:
    """The sources in Postulo's order, as `capture` runs them."""
    for source_class in BUILTIN_SOURCES:
        source = source_class()
        if not source.can_handle(url):
            continue
        try:
            data = source.parse(url, html)
        except Exception:  # a source that throws is skipped, as capture skips it
            data = None
        if data is not None:
            return SOURCE_NAMES[source_class.__name__], data
    return None, None


def cases() -> list[pathlib.Path]:
    return sorted(FIXTURES.glob("*.json"))


def test_there_is_a_corpus():
    """One that quietly emptied would pass every assertion below it."""
    assert len(cases()) >= 6, f"only {len(cases())} fixtures in {FIXTURES}"


@pytest.mark.parametrize("path", cases(), ids=lambda path: path.stem)
def test_a_page_reads_as_expected(path: pathlib.Path):
    expected = json.loads(path.read_text(encoding="utf-8"))
    html = (FIXTURES / f"{path.stem}.html").read_text(encoding="utf-8")

    source, data = read(expected["url"], html)

    assert data is not None, f"{path.stem}: the page was not read at all ({expected['why']})"
    assert source == expected["source"], f"{path.stem}: read by the wrong source"

    got = json.loads(data.model_dump_json())
    # Field by field rather than one comparison: a whole posting printed as a diff is
    # unreadable, and the field that is wrong is the thing worth naming.
    for field, want in expected["data"].items():
        assert got.get(field) == want, f"{path.stem}.{field}"
    assert set(got) == set(expected["data"]), f"{path.stem}: the fixture states every field"
