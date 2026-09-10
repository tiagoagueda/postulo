"""Resizing a column in a real browser (#136).

A width is the one preference in a table that can only be *asked for* with a pointer, so
this is the one place a script is unavoidable — and therefore the one place where the
browser is the only honest test. The storage and the bounds are covered by
`tests/test_table_shape.py`; what is here is the handle: that it exists only where a script
runs, that the keyboard can reach it, and that the width survives a reload.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.conftest import EMAIL, PASSWORD

pytestmark = pytest.mark.e2e


def sign_in(page: Page, base: str) -> None:
    page.goto(f"{base}/accounts/login/")
    page.locator("input[name=login]").fill(EMAIL)
    page.locator("input[name=password]").fill(PASSWORD)
    page.locator("form").get_by_role("button", name="Sign In", exact=True).click()
    expect(page).to_have_url(f"{base}/")


def open_the_table(page: Page, live_server, applicant):
    from postulo.jobs.models import Company

    Company.objects.create(owner=applicant, name="Aperture Science")
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/jobs/companies/")


def name_header(page: Page):
    return page.locator('th[data-col="name"]')


def press_and_wait(page: Page, handle, key: str) -> None:
    """A key, and the save it starts, finished before the test moves on.

    Waited for rather than slept through: the save is a background request, and a test that
    ends while one is in flight tears the server down underneath it -- which showed up as
    two errors in a full run and as nothing at all in isolation.
    """
    with page.expect_response(lambda r: "/settings/" in r.url and r.request.method == "POST"):
        handle.press(key)


def test_the_handle_is_added_where_a_script_runs(page: Page, live_server, applicant):
    open_the_table(page, live_server, applicant)

    expect(name_header(page).locator("[data-col-handle]")).to_have_count(1)


def test_the_keyboard_widens_a_column(page: Page, live_server, applicant):
    """A control only a mouse can reach is a control half the people using this cannot use."""
    open_the_table(page, live_server, applicant)
    header = name_header(page)
    before = header.bounding_box()["width"]

    handle = header.locator("[data-col-handle]")
    handle.focus()
    for _ in range(4):
        press_and_wait(page, handle, "ArrowRight")

    expect(header).not_to_have_attribute("style", "")
    assert header.bounding_box()["width"] > before


def test_a_width_survives_a_reload(page: Page, live_server, applicant):
    """A preference follows the person, which is the whole reason it is not in the URL."""
    open_the_table(page, live_server, applicant)
    handle = name_header(page).locator("[data-col-handle]")
    handle.focus()
    for _ in range(4):
        press_and_wait(page, handle, "ArrowRight")

    page.reload()

    style = name_header(page).get_attribute("style") or ""
    assert "width:" in style


def test_home_lets_the_column_size_itself_again(page: Page, live_server, applicant):
    """A column dragged too narrow once must not be too narrow for ever."""
    open_the_table(page, live_server, applicant)
    handle = name_header(page).locator("[data-col-handle]")
    handle.focus()
    press_and_wait(page, handle, "ArrowRight")

    press_and_wait(page, handle, "Home")
    page.reload()

    style = name_header(page).get_attribute("style") or ""
    assert "width:" not in style


def test_dragging_it_widens_the_column(page: Page, live_server, applicant):
    open_the_table(page, live_server, applicant)
    header = name_header(page)
    before = header.bounding_box()["width"]
    box = header.locator("[data-col-handle]").bounding_box()

    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.mouse.down()
    page.mouse.move(box["x"] + 120, box["y"] + box["height"] / 2, steps=8)
    with page.expect_response(lambda r: "/settings/" in r.url and r.request.method == "POST"):
        page.mouse.up()

    assert header.bounding_box()["width"] > before


def test_the_third_click_on_a_header_stops_sorting(page: Page, live_server, applicant):
    """Tri-state, through the page rather than through the method."""
    open_the_table(page, live_server, applicant)

    header = page.locator('th[data-col="postings"]')
    header.locator("a").click()
    expect(header).to_have_attribute("aria-sort", "descending")

    header.locator("a").click()
    expect(header).to_have_attribute("aria-sort", "ascending")

    header.locator("a").click()
    expect(header).not_to_have_attribute("aria-sort", "ascending")
    expect(header).not_to_have_attribute("aria-sort", "descending")
