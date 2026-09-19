"""Two rendering modes axe never runs in: Windows High Contrast, and paper (#277).

Both themes are checked on every page; these two are switched on by the operating system
or by the print dialogue, and nothing had looked at either until a button turned out to be
an ordinary word and a dark profile printed white on white.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page

from .conftest import EMAIL, PASSWORD
from .test_accessibility import furnished  # noqa: F401

pytestmark = pytest.mark.e2e


def sign_in(page: Page, base: str) -> None:
    page.goto(f"{base}/accounts/login/")
    page.locator("input[name=login]").fill(EMAIL)
    page.locator("input[name=password]").fill(PASSWORD)
    page.locator("form").get_by_role("button", name="Sign In", exact=True).click()


def test_a_button_keeps_a_border_under_forced_colours(page: Page, live_server, furnished):  # noqa: F811
    """Preflight zeroes every border and a button had none, so `Save` and `Sign out` were
    drawn as bare words."""
    sign_in(page, live_server.url)
    page.emulate_media(forced_colors="active")
    page.goto(f"{live_server.url}/jobs/companies/")

    border = page.locator("a.btn").first.evaluate(
        """el => {
            const style = getComputedStyle(el);
            return {style: style.borderTopStyle, width: style.borderTopWidth};
        }"""
    )
    assert border["style"] == "solid" and border["width"] == "1px", border


def test_the_funnel_bars_keep_their_value_under_forced_colours(page: Page, live_server, furnished):  # noqa: F811
    """A track and a value that are both background colours are both repainted to Canvas;
    the value opts out and takes Highlight, so the chart survives with its numbers."""
    sign_in(page, live_server.url)
    page.emulate_media(forced_colors="active")
    # The cadence view, whose weekly bars hold the application sent thirty days ago; the
    # month's funnel may be empty.
    page.goto(f"{live_server.url}/applications/report/?period=weeks&weeks=8")

    # `getComputedStyle` says nothing true about a progress bar's pseudo-elements under
    # forced colours -- Chromium reports the value's paint as white whatever the rule says --
    # so the bar is photographed and its pixels read. `Highlight` in the emulated palette
    # is rgb(55, 0, 110), found by painting it on a plain element and reading that back.
    import io

    from PIL import Image

    # A bar with something in it: the first on the page may be an empty stage.
    index = page.evaluate(
        "() => [...document.querySelectorAll('progress.funnel-bar')].findIndex(b => b.value > 0)"
    )
    assert index >= 0, "no bar on the report has a value"
    bar = page.locator("progress.funnel-bar").nth(index)
    highlight = page.evaluate(
        """() => {
            const probe = document.createElement('div');
            probe.style.cssText = 'forced-color-adjust: none; background-color: Highlight';
            document.body.appendChild(probe);
            const colour = getComputedStyle(probe).backgroundColor;
            probe.remove();
            return colour.match(/[0-9]+/g).slice(0, 3).map(Number);
        }"""
    )
    assert bar.evaluate("el => getComputedStyle(el).forcedColorAdjust") == "none"
    assert bar.evaluate("el => getComputedStyle(el).borderTopStyle") == "solid"
    image = Image.open(io.BytesIO(bar.screenshot())).convert("RGB")
    pixels = {image.getpixel((x, y)) for x in range(image.width) for y in range(image.height)}
    # A six-pixel bar is photographed with its edges blended, and the palette differs by
    # platform -- rgb(55, 0, 110) on Windows, rgb(5, 0, 73) on Linux -- so the question is
    # whether the bar carries Highlight's hue anywhere: a pixel that leans to blue the way
    # Highlight does, against a track and a border that are grey.
    red, green, blue = highlight
    assert blue > max(red, green), f"the emulated Highlight is not blue-leaning: {highlight}"
    tinted = [p for p in pixels if p[2] - max(p[0], p[1]) > 20]
    assert tinted, f"no Highlight-tinted pixel on the bar: {sorted(pixels)[:8]}"


def test_a_dark_profile_prints_as_ink_on_white(page: Page, live_server, furnished):  # noqa: F811
    """A dark root and a browser that drops backgrounds printed near-white text on white;
    on paper the ink scale is turned over, the tables show whole, and the chrome stays off."""
    profile = furnished["applicant"].profile
    profile.theme = "dark"
    profile.save(update_fields=["theme"])
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/jobs/companies/")
    assert page.evaluate("() => document.documentElement.dataset.theme") == "dark"

    page.emulate_media(media="print")
    seen = page.evaluate(
        """() => {
            const toRgb = (c) => c.match(/[0-9.]+/g).slice(0, 3).map(Number);
            const body = getComputedStyle(document.body);
            const text = toRgb(body.color);
            const box = document.querySelector('.scroll-x');
            return {
                textLuma: (text[0] + text[1] + text[2]) / 3,
                background: body.backgroundColor,
                overflow: box ? getComputedStyle(box).overflowX : null,
                header: getComputedStyle(document.querySelector('[data-site-header]')).display,
                footer: getComputedStyle(document.querySelector('footer')).display,
            };
        }"""
    )
    assert seen["textLuma"] < 100, f"the text is not dark: {seen}"
    assert seen["background"] in ("rgb(255, 255, 255)", "rgba(0, 0, 0, 0)"), seen
    assert seen["overflow"] == "visible"
    assert seen["header"] == "none" and seen["footer"] == "none"
