"""Dragging a widget into place, in a real browser (#125).

Where a drop *puts* a widget is covered by `tests/test_dashboard_grid.py`, which needs no
browser for it. What needs one is the gesture: that nothing is draggable until a script
says so, that a drop goes through the same form the four arrows post to, and that those
arrows are still there afterwards — which is the rule every drag in this application obeys,
because drag and drop fires on neither a touch screen nor a keyboard.
"""

from __future__ import annotations

from itertools import pairwise

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


def open_the_arrange_page(page: Page, live_server) -> None:
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/settings/dashboard/")


def drag(page: Page, row, onto) -> None:
    """A real HTML5 drag, which Playwright's high-level helpers do not perform."""
    page.evaluate(
        """([row, onto]) => {
            const transfer = new DataTransfer();
            const fire = (target, type) => target.dispatchEvent(
                new DragEvent(type, {bubbles: true, cancelable: true, dataTransfer: transfer})
            );
            fire(row, 'dragstart');
            fire(onto, 'dragover');
            fire(onto, 'drop');
            fire(row, 'dragend');
        }""",
        [row, onto],
    )


def order(applicant) -> list[str]:
    from postulo.accounts.models import Profile

    return Profile.objects.get(user=applicant).dashboard_widgets


def rows(page: Page):
    return page.locator("[data-widget-list] [data-widget-row]")


def test_the_rows_become_draggable_only_where_a_script_runs(page: Page, live_server, applicant):
    """The template draws no `draggable`; the script adds it. An affordance that does
    nothing is worse than none.
    """
    open_the_arrange_page(page, live_server)

    expect(rows(page).first).to_have_attribute("draggable", "true")


def test_dropping_one_onto_another_moves_it(page: Page, live_server, applicant):
    open_the_arrange_page(page, live_server)
    before = order(applicant)

    third = rows(page).nth(2).element_handle()
    drag(page, rows(page).first.element_handle(), third)
    page.wait_for_load_state("networkidle")

    after = order(applicant)
    assert after[0] != before[0], "the one that was first has moved"
    assert after.index(before[0]) == 2, "to where it was dropped"
    assert sorted(after) == sorted(before), "and nothing was gained or lost"


def test_a_drop_is_saved_where_the_arrows_save(page: Page, live_server, applicant):
    """No second store and no new endpoint: the page reloads from the arrangement, so what
    a drop leaves behind is what the next visit reads.
    """
    open_the_arrange_page(page, live_server)

    drag(page, rows(page).first.element_handle(), rows(page).nth(2).element_handle())
    page.wait_for_load_state("networkidle")
    landed = [row.get_attribute("data-widget-row") for row in rows(page).all()]

    page.goto(f"{live_server.url}/settings/dashboard/")
    assert [row.get_attribute("data-widget-row") for row in rows(page).all()] == landed


def test_a_drop_says_where_it_landed(page: Page, live_server, applicant):
    """The same sentence a keyboard move gets, because it is the same move."""
    open_the_arrange_page(page, live_server)

    drag(page, rows(page).first.element_handle(), rows(page).nth(2).element_handle())
    page.wait_for_load_state("networkidle")

    expect(page.locator('[role="status"], [role="alert"]').first).to_contain_text("row")


def test_the_arrows_are_still_there_afterwards(page: Page, live_server, applicant):
    """Dragging is an addition to the control that works everywhere, never a replacement."""
    open_the_arrange_page(page, live_server)

    drag(page, rows(page).first.element_handle(), rows(page).nth(2).element_handle())
    page.wait_for_load_state("networkidle")

    moved = rows(page).nth(2)
    for way in ("up a row", "down a row", "one place earlier", "one place later"):
        assert moved.get_by_role("button", name=way, exact=False).count() == 1


def test_the_dashboard_lays_out_in_four_columns(page: Page, live_server, applicant):
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/")

    grid = page.locator("[data-widget]").first.locator("xpath=..")

    assert "lg:grid-cols-4" in (grid.get_attribute("class") or "")


def test_a_narrow_screen_reads_downwards(page: Page, live_server, applicant):
    """One column, in the order the person arranged. #73 is where the phone gets its own
    attention; this is only what the grid does in the meantime.
    """
    page.set_viewport_size({"width": 390, "height": 844})
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/")

    boxes = [row.bounding_box() for row in page.locator("[data-widget]").all()[:3]]

    for earlier, later in pairwise(boxes):
        assert later["y"] >= earlier["y"] + earlier["height"] - 1, "stacked, not side by side"
