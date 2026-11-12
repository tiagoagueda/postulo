"""The mark in the header, drawn by a browser (#1238).

The vector is what is shown, the PNG only behind it. The moving mark is offered to
somebody who switched it on, and a browser whose system asks for less motion is given the
still vector instead -- including the moment that request changes, with no reload. What a
document cannot say is which of the sources a browser actually took; `currentSrc` does.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page

from tests.e2e.signing_in import sign_in

pytestmark = pytest.mark.e2e

MARK = "header [data-mark]"


def shown(page: Page, *, moving: bool | None = None) -> str:
    """The file the header's image was drawn from, once loaded (and moving or not, if asked).

    Polled from here rather than with `wait_for_function`, whose predicate is an `eval`
    that the page's content security policy refuses."""
    for _ in range(100):
        drawn = page.evaluate(
            "(selector) => { const i = document.querySelector(selector);"
            " return i.complete ? i.currentSrc : ''; }",
            MARK,
        )
        name = drawn.rsplit("/", 1)[-1]
        if name and (moving is None or ("drift" in name) is moving):
            return name
        page.wait_for_timeout(50)
    raise AssertionError(f"the mark is still {drawn!r}")


def test_the_header_draws_the_vector_at_28_pixels_beside_the_name(
    page: Page, live_server, applicant
):
    sign_in(page, live_server.url, "/")

    assert shown(page).startswith("logo.") and shown(page).endswith(".svg")
    box = page.locator(MARK).bounding_box()
    assert (round(box["width"]), round(box["height"])) == (28, 28)
    name = page.locator("header [data-wordmark]").bounding_box()
    middle = box["y"] + box["height"] / 2
    assert name["y"] <= middle <= name["y"] + name["height"], "in line with the name"
    assert page.locator(MARK).get_attribute("alt") == ""


def test_the_moving_mark_is_still_for_a_system_that_asks_for_less_motion(
    page: Page, live_server, applicant
):
    applicant.profile.animated_mark = True
    applicant.profile.save()

    page.emulate_media(reduced_motion="reduce")
    sign_in(page, live_server.url, "/")
    assert "drift" not in shown(page), "still, whatever the setting says"

    page.emulate_media(reduced_motion="no-preference")
    assert "drift" in shown(page, moving=True), (
        "moving for somebody who asked and whose system does not mind"
    )

    page.emulate_media(reduced_motion="reduce")
    assert "drift" not in shown(page, moving=False), "and still again the moment it does"


def test_nobody_gets_the_moving_mark_without_asking(page: Page, live_server, applicant):
    page.emulate_media(reduced_motion="no-preference")
    sign_in(page, live_server.url, "/")

    assert "drift" not in shown(page)
