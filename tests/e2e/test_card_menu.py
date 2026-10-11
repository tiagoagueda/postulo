"""The pencil menu at a board card's corner (#845): with and without a script, and in the corner.

The moves it makes are held where they always were (`test_board_drag.py`, `test_board_fold.py`);
these are the parts that are new: that the menu opens and moves a card with scripts off, that
the pencil never sits over the card's title, and that the menu is reachable by the keyboard.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Browser, Page, expect

from tests.e2e.card_menu import choose_status, open_menu, row, trigger_of
from tests.e2e.signing_in import sign_in

from .test_application_filters import search  # noqa: F401
from .test_reflow import SCROLLS_SIDEWAYS

pytestmark = pytest.mark.e2e


def a_card(page: Page, live_server, search, width: int = 1280):  # noqa: F811
    from postulo.applications.models import Application

    application = Application.objects.get(posting__title="Resonance Technician")
    sign_in(page, live_server.url)
    page.set_viewport_size({"width": width, "height": 800})
    page.goto(f"{live_server.url}/applications/?view=board")
    return application, page.locator(f"[data-card='{application.pk}']")


@pytest.mark.parametrize("width", [1280, 390, 320])
def test_the_pencil_is_in_the_corner_and_never_over_the_title(
    page: Page,
    live_server,
    search,  # noqa: F811
    width,
):
    _application, card = a_card(page, live_server, search, width)
    pencil = trigger_of(card).bounding_box()
    outer = card.bounding_box()
    # Where the title's words end, not its box: the link is padded clear of the corner, and
    # that padding is empty space the pencil sits in.
    words_end = card.locator("a").first.evaluate(
        """(a) => {
            const range = document.createRange();
            range.selectNodeContents(a);
            return Math.max(...Array.from(range.getClientRects(), (r) => r.right));
        }"""
    )

    assert pencil["y"] < outer["y"] + 16, "at the top of the card"
    assert pencil["x"] + pencil["width"] <= outer["x"] + outer["width"], "inside the card"
    assert words_end <= pencil["x"] + 0.5, "the title's words end before the pencil begins"
    assert pencil["width"] >= 24 and pencil["height"] >= 24, "a target a thumb can find"
    assert not page.evaluate(SCROLLS_SIDEWAYS)["reached"]


def test_the_menu_opens_from_the_keyboard_and_a_row_moves_the_card(
    page: Page,
    live_server,
    search,  # noqa: F811
):
    application, card = a_card(page, live_server, search)
    trigger_of(card).focus()

    page.keyboard.press("Enter")
    expect(card.locator("[data-card-status-menu] [data-popover]")).to_be_visible()
    page.keyboard.press("Escape")
    expect(card.locator("[data-card-status-menu] [data-popover]")).to_be_hidden()
    expect(trigger_of(card)).to_be_focused()

    with page.expect_navigation():
        choose_status(card, "screening")
    application.refresh_from_db()
    assert application.status == "screening"


def test_with_scripts_off_the_menu_still_opens_and_moves_the_card(
    browser: Browser,
    live_server,
    search,  # noqa: F811
):
    from postulo.applications.models import Application

    application = Application.objects.get(posting__title="Resonance Technician")
    context = browser.new_context(java_script_enabled=False)
    page = context.new_page()
    try:
        sign_in(page, live_server.url)
        page.goto(f"{live_server.url}/applications/?view=board")
        card = page.locator(f"[data-card='{application.pk}']")

        open_menu(card)
        assert row(card, "screening").is_visible()
        with page.expect_navigation():
            row(card, "screening").click()

        application.refresh_from_db()
        assert application.status == "screening"
    finally:
        context.close()
