"""The breadcrumb as a thin line that opens while it is pointed at or focused (#832)."""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from postulo.jobs.models import Company
from tests.e2e.signing_in import sign_in

pytestmark = pytest.mark.e2e


@pytest.fixture
def company(applicant):
    applicant.profile.show_breadcrumb = True
    applicant.profile.breadcrumb_on_hover = True
    applicant.profile.save()
    return Company.objects.create(owner=applicant, name="Aperture")


def header_height(page: Page) -> float:
    return page.evaluate(
        "document.querySelector('[data-site-header]').getBoundingClientRect().height"
    )


def test_it_is_a_line_until_pointed_at_and_the_header_does_not_grow(
    page: Page, live_server, company
):
    sign_in(page, live_server.url, company.get_absolute_url())
    trail = page.locator("[data-breadcrumb] > nav")
    bar = page.locator("[data-breadcrumb]")
    shut = header_height(page)
    expect(trail).to_have_css("opacity", "0")
    assert bar.bounding_box()["height"] < 12, "a line a few pixels high"

    bar.hover(position={"x": 40, "y": 2})

    expect(trail).to_have_css("opacity", "1")
    assert header_height(page) == shut, "drawn over the page, so nothing below it moves"
    assert trail.bounding_box()["y"] >= bar.bounding_box()["y"]


def test_the_keyboard_reaches_it_and_escape_puts_it_away(page: Page, live_server, company):
    sign_in(page, live_server.url, company.get_absolute_url())
    trail = page.locator("[data-breadcrumb] > nav")
    link = page.locator("[data-breadcrumb] a").first

    link.focus()
    expect(trail).to_have_css("opacity", "1")

    page.keyboard.press("Escape")

    expect(trail).to_have_css("opacity", "0")


def test_escape_keeps_it_shut_while_the_pointer_stays_and_it_comes_back_after(
    page: Page, live_server, company
):
    sign_in(page, live_server.url, company.get_absolute_url())
    trail = page.locator("[data-breadcrumb] > nav")
    bar = page.locator("[data-breadcrumb]")

    bar.hover(position={"x": 40, "y": 2})
    expect(trail).to_have_css("opacity", "1")
    page.keyboard.press("Escape")
    expect(trail).to_have_css("opacity", "0")
    expect(bar).to_have_attribute("data-dismissed", "")

    page.mouse.move(600, 500)
    expect(bar).not_to_have_attribute("data-dismissed", "")
    bar.hover(position={"x": 40, "y": 2})
    expect(trail).to_have_css("opacity", "1")


def test_the_line_is_a_real_border_so_forced_colours_still_show_it(
    page: Page, live_server, company
):
    page.emulate_media(forced_colors="active")
    sign_in(page, live_server.url, company.get_absolute_url())

    style = page.locator("[data-breadcrumb]").evaluate(
        "(e) => { const s = getComputedStyle(e);"
        " return [s.borderBottomWidth, s.borderBottomStyle]; }"
    )

    assert style[1] == "solid" and float(style[0].removesuffix("px")) >= 2


def test_a_touch_screen_has_no_hover_so_the_trail_is_drawn_whole(browser, live_server, company):
    context = browser.new_context(
        has_touch=True, is_mobile=False, viewport={"width": 1280, "height": 900}
    )
    page = context.new_page()
    try:
        # `(hover: hover) and (pointer: fine)` is false when the primary input is coarse.
        page.emulate_media(media="screen")
        sign_in(page, live_server.url, company.get_absolute_url())
        coarse = page.evaluate("matchMedia('(hover: hover) and (pointer: fine)').matches")
        if coarse:
            pytest.skip("this browser build still reports a fine pointer with touch on")
        expect(page.locator("[data-breadcrumb] > nav")).to_have_css("opacity", "1")
    finally:
        context.close()
