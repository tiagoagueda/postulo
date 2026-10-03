"""A sort answered slowly must not land over a filter typed after it (#648).

The sort and page links are on the filter form's turn (`hx-sync`), so a newer filter
replaces them while they are in flight. Before, the late sort answer was drawn into a table
the filter's answer had already replaced: the address said the sort, the rows said the
filter, the header's boxes were gone and focus fell to the body.
"""

from __future__ import annotations

import contextlib
import re

import pytest
from playwright.sync_api import Page, expect

from .test_accessibility import furnished, sign_in  # noqa: F401

pytestmark = pytest.mark.e2e

#: Where each table lives, the id of its sort link, and the box of the filter typed.
TABLES = [
    ("/jobs/companies/", "companies-table", "sort-name"),
    ("/listings/?state=all", "listings-table", "sort-title"),
    ("/applications/", "applications-table", "sort-role"),
]


@pytest.mark.parametrize(
    ("path", "table", "sort"), TABLES, ids=["companies", "listings", "applications"]
)
def test_a_slow_sort_does_not_land_over_a_newer_filter(
    live_server,
    page: Page,
    furnished,  # noqa: F811
    path,
    table,
    sort,
):
    page.set_viewport_size({"width": 1280, "height": 800})
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}{path}")

    link = page.locator(f"#{sort}")
    asked = link.get_attribute("hx-get")
    held = []
    page.route(lambda url: url.endswith(asked), lambda route: held.append(route))

    link.click()
    for _ in range(50):
        if held:
            break
        page.wait_for_timeout(50)
    assert len(held) == 1, "the sort's request is out, and held"

    page.locator('[data-col="location"] summary').click()
    page.locator("#filter-location").fill("a")
    expect(page).to_have_url(re.compile(r"[?&]location=a(&|$)"))

    # Let the old answer go: it must not be drawn.
    with contextlib.suppress(Exception):  # already abandoned, which is the point
        held[0].continue_()
    page.wait_for_timeout(400)

    assert asked not in page.url, page.url
    expect(page.locator(f"#{table}")).to_be_visible()
    assert page.locator(f"#{table} #filter-location").count() == 1, "the header kept its boxes"
    expect(page.locator("#filter-location")).to_have_value("a")
    assert page.evaluate("document.activeElement.id") == "filter-location", "focus fell to the body"
