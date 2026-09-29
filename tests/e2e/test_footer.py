"""The foot of every page, measured: one line on a wide screen, two on a phone, and never
behind the bar a phone's navigation becomes (#212).

`test_footer.py` holds what it says to whom; this holds what only a browser can answer --
how many lines the words take at a width, and where they are when the page has scrolled to
its end. The case measured is the longest the footer can be: somebody signed in, so the
version is there, and an operator who has set a legal notice, so all four links are.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page

from .test_accessibility import furnished, sign_in  # noqa: F401
from .test_navigation_bar import bar_top

pytestmark = pytest.mark.e2e

#: How many rows the footer's pieces sit on, and where its box ends. A piece is the
#: instance's line or one item of the list; two pieces whose tops are within a few pixels
#: of each other share a row.
ROWS = """() => {
  const footer = document.querySelector('body > footer');
  const pieces = [footer.querySelector(':scope > p'), ...footer.querySelectorAll('li')];
  const tops = pieces.map((el) => el.getBoundingClientRect().top).sort((a, b) => a - b);
  let rows = 0;
  let last = -Infinity;
  for (const top of tops) {
    if (top - last > 4) rows += 1;
    last = top;
  }
  const box = footer.getBoundingClientRect();
  return {rows, bottom: box.bottom, height: box.height,
          scrolls: document.documentElement.scrollWidth > document.documentElement.clientWidth};
}"""

#: A wide screen, the phone most of them report, and 400% zoom on a 1280-pixel window.
WIDE = {"width": 1280, "height": 900}
PHONE = {"width": 390, "height": 740}
NARROW = {"width": 320, "height": 640}


@pytest.fixture
def everything_set(settings):
    settings.POSTULO_LEGAL_NOTICE_URL = "https://example.org/impressum"


def measure(page: Page, size: dict) -> dict:
    page.set_viewport_size(size)
    page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
    page.wait_for_timeout(100)
    return page.evaluate(ROWS)


def test_one_line_on_a_wide_screen_and_two_on_a_phone(
    live_server,
    page: Page,
    furnished,  # noqa: F811
    everything_set,
    settings,
):
    """At 320 -- 400% zoom, not a phone anybody holds -- four links in English are wider
    than the line, so a legal notice takes a third row there; without one it is two."""
    base = live_server.url
    sign_in(page, base)
    page.goto(f"{base}/applications/")

    wide = measure(page, WIDE)
    phone = measure(page, PHONE)
    narrow = measure(page, NARROW)

    assert wide["rows"] == 1, wide
    assert phone["rows"] <= 2, phone
    assert narrow["rows"] <= 3 and not narrow["scrolls"], narrow

    settings.POSTULO_LEGAL_NOTICE_URL = ""
    page.reload()
    assert measure(page, NARROW)["rows"] <= 2


@pytest.mark.parametrize("size", [PHONE, NARROW], ids=["390", "320"])
def test_the_whole_footer_clears_the_bar(
    live_server,
    page: Page,
    furnished,  # noqa: F811
    everything_set,
    size,
):
    """The bar is fixed to the foot of the window and the page's bottom padding is its
    height, so at the end of the page the footer's own box -- its padding included -- has
    to end where the bar begins, or above it."""
    base = live_server.url
    sign_in(page, base)
    page.set_viewport_size(size)
    page.goto(f"{base}/applications/")

    seen = measure(page, size)

    assert seen["bottom"] <= bar_top(page) + 1, (seen, bar_top(page))
