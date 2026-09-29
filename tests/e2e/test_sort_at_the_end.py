"""The sort sits at the end of its header cell, whatever the column holds (#317).

`tests/test_tables.py` reads the classes. What it cannot say is where the browser puts the
control: that the icons of a row of headers line up down the end edge of their columns
instead of following each name wherever it stops, that the end is the left in Arabic, that
a numeric column's name sits at the end beside its icon, and that the resize handle the
script pins to the same edge leaves the whole 24 pixels of the sort to be clicked.

Every measurement here is in the reading direction's own terms: a box's *start* and *end*
are its left and right in English and its right and left in Arabic, so one set of
assertions holds for both.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from postulo.applications.tables import ApplicationsTable
from postulo.jobs.tables import CompaniesTable, ListingsTable

from .test_accessibility import furnished, sign_in  # noqa: F401

pytestmark = pytest.mark.e2e

#: The criterion's number: the sort is icon-only, and keeps a 24-pixel target (SC 2.5.8).
TARGET = 24

#: `gap-1` between a header's name and its sort.
GAP = 4

#: A pixel either way for the browser's rounding.
SLACK = 1

#: Every table drawn with `<c-table.head>`, and the columns in it whose figures sit at the end.
TABLES = {
    "/jobs/companies/": {c.key for c in CompaniesTable.columns if c.numeric},
    "/listings/": {c.key for c in ListingsTable.columns if c.numeric},
    "/applications/": {c.key for c in ApplicationsTable.columns if c.numeric},
}

MEASURE = """() => {
  const logical = (element, rtl) => {
    if (!element) return null;
    const box = element.getBoundingClientRect();
    return {
      start: rtl ? -box.right : box.left,
      end: rtl ? -box.left : box.right,
      top: box.top,
      bottom: box.bottom,
      width: box.width,
      height: box.height,
    };
  };
  return [...document.querySelectorAll('thead th[data-col]')]
    .filter((cell) => cell.querySelector('a[id^="sort-"]'))
    .map((cell) => {
      const style = getComputedStyle(cell);
      const rtl = style.direction === 'rtl';
      const row = cell.querySelector(':scope > div');
      const name = row.querySelector(':scope > details > summary') || row.firstElementChild;
      return {
        key: cell.dataset.col,
        paddingStart: parseFloat(style.paddingInlineStart),
        paddingEnd: parseFloat(style.paddingInlineEnd),
        cell: logical(cell, rtl),
        name: logical(name, rtl),
        sort: logical(cell.querySelector('a[id^="sort-"]'), rtl),
        handle: logical(cell.querySelector('[data-col-handle]'), rtl),
      };
    });
}"""


def reading(language: str, applicant) -> None:
    profile = applicant.profile
    profile.language = language
    profile.save(update_fields=["language"])


def headers_on(page: Page, url: str, *, rtl: bool) -> list[dict]:
    page.goto(url)
    expect(page.locator("html")).to_have_attribute("dir", "rtl" if rtl else "ltr")
    # The handle is the script's; measuring before it arrives would miss the overlap.
    expect(page.locator("thead [data-col-handle]").first).to_be_attached()
    headers = page.evaluate(MEASURE)
    assert headers, f"{url} drew no sortable header to measure"
    return headers


def assert_at_the_end(header: dict, where: str) -> None:
    """The sort's end edge is the cell's, give or take the cell's own padding."""
    cell, sort = header["cell"], header["sort"]
    assert cell["end"] - sort["end"] <= header["paddingEnd"] + SLACK, (
        f"{where}: the sort sits {cell['end'] - sort['end']:.0f}px from the end of its cell, "
        f"which pads {header['paddingEnd']:.0f}px"
    )
    assert sort["width"] >= TARGET and sort["height"] >= TARGET, f"{where}: under 24 pixels"
    handle = header["handle"]
    if handle:
        overlap = min(sort["end"], handle["end"]) - max(sort["start"], handle["start"])
        assert overlap <= 0, f"{where}: the resize handle covers {overlap:.0f}px of the sort"


@pytest.mark.parametrize("language", ["en", "ar"])
def test_every_sort_sits_at_the_end_of_its_header(
    live_server,
    page: Page,
    furnished,  # noqa: F811
    language,
):
    """*Companies* first, which is where the review found it, and then every other table
    the component draws: in English the end is the right, in Arabic the left."""
    reading(language, furnished["applicant"])
    sign_in(page, live_server.url)

    for path, numeric in TABLES.items():
        for header in headers_on(page, f"{live_server.url}{path}", rtl=language == "ar"):
            where = f"{path} {header['key']} {language}"
            assert_at_the_end(header, where)
            name, sort, cell = header["name"], header["sort"], header["cell"]
            if header["key"] in numeric:
                assert sort["start"] - name["end"] <= GAP + SLACK, (
                    f"{where}: a numeric column's name belongs beside its sort, at the end"
                )
            else:
                assert name["start"] - cell["start"] <= header["paddingStart"] + SLACK, (
                    f"{where}: the name should start where the cell does"
                )


#: The role, and three columns with two-word names that sort but do not filter. A column
#: that filters holds its name on one line inside its disclosure, and every column of
#: *Companies* filters; these are names nothing holds, which wrap once a phone squeezes the
#: table.
WRAPPING = ["role", "last_activity", "next_reminder", "next_interview"]


@pytest.mark.parametrize("language", ["en", "ar"])
def test_a_name_that_wraps_keeps_its_sort_beside_its_first_line(
    live_server,
    page: Page,
    furnished,  # noqa: F811
    language,
):
    """At 320 pixels the table is laid out as narrow as its words allow, and a name of two
    words goes onto two lines. The sort stays at the top, beside where the name starts,
    rather than centred on the whole of it and belonging to neither line."""
    applicant = furnished["applicant"]
    profile = applicant.profile
    profile.table_settings = {"applications": {"columns": WRAPPING}}
    profile.save(update_fields=["table_settings"])
    reading(language, applicant)
    page.set_viewport_size({"width": 320, "height": 800})
    sign_in(page, live_server.url)

    headers = headers_on(page, f"{live_server.url}/applications/", rtl=language == "ar")
    wrapped = [h for h in headers if h["name"]["height"] > h["sort"]["height"] + SLACK]
    assert wrapped, "no header wrapped at 320 pixels, so this measured nothing"

    for header in wrapped:
        where = f"/applications/ {header['key']} {language}"
        name, sort = header["name"], header["sort"]
        assert abs(sort["top"] - name["top"]) <= SLACK, f"{where}: the sort left the first line"
        assert (sort["top"] + sort["bottom"]) / 2 < (name["top"] + name["bottom"]) / 2, (
            f"{where}: the sort is centred on the wrapped name"
        )
        assert_at_the_end(header, where)
