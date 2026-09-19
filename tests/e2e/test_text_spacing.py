"""Text spacing (SC 1.4.12), 200% zoom, and reduced motion, measured in a browser (#278).

A page must lose nothing when a reader overrides line height to 1.5, paragraph spacing to
twice the font size, letter spacing to 0.12em and word spacing to 0.16em -- the standard
override low-vision and dyslexic readers apply -- and nothing when the window is zoomed to
200%. Neither had ever been run, and the stylesheet has the shapes that break under them:
`truncate`, `whitespace-nowrap`, fixed heights. The same measure-in-the-browser shape as
the target-size and reflow checks: the override is injected, every page is visited, and
any box that clips its own words, or a page that scrolls sideways, is named.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page

from .conftest import PASSWORD
from .test_accessibility import furnished, sign_in, signed_in_paths  # noqa: F401
from .test_reflow import SCROLLS_SIDEWAYS

pytestmark = pytest.mark.e2e

#: The criterion's override, as its understanding document writes it.
TEXT_SPACING = """
  * {
    line-height: 1.5 !important;
    letter-spacing: 0.12em !important;
    word-spacing: 0.16em !important;
  }
  p { margin-bottom: 2em !important; }
"""

#: 200% zoom on a 1280-pixel window is a 640-pixel layout viewport.
ZOOMED = 640

#: A text-bearing box that clips its own words: overflow hidden or clipped in a direction
#: its content exceeds by more than a pixel. A scroll box is not one -- it scrolls, which
#: is the point of it -- and neither is anything screen-reader-only, whose box is clipped
#: to nothing on purpose.
CLIPPED = r"""() => {
  const out = [];
  const seen = new Set();
  for (const el of document.querySelectorAll('body *')) {
    if (!el.textContent.trim()) continue;
    if (el.closest('.sr-only, [hidden], svg')) continue;
    const style = getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden') continue;
    if (style.clip === 'rect(0px, 0px, 0px, 0px)') continue;
    if (el.clientWidth <= 1 && el.clientHeight <= 1) continue;  // screen-reader-only, by size
    const hidesX = ['hidden', 'clip'].includes(style.overflowX);
    const hidesY = ['hidden', 'clip'].includes(style.overflowY);
    if (!hidesX && !hidesY) continue;
    const overX = hidesX && el.scrollWidth > el.clientWidth + 1;
    const overY = hidesY && el.scrollHeight > el.clientHeight + 1;
    if (!overX && !overY) continue;
    // A line clamped on purpose says so, and the ellipsis is the reader's cue.
    if (style.webkitLineClamp && style.webkitLineClamp !== 'none') continue;
    const what = el.outerHTML.replace(/\s+/g, ' ').slice(0, 120);
    if (seen.has(what)) continue;
    seen.add(what);
    out.push({
      what,
      needs: Math.round(overX ? el.scrollWidth : el.scrollHeight),
      box: Math.round(overX ? el.clientWidth : el.clientHeight),
      axis: overX ? 'wide' : 'tall',
    });
  }
  return out;
}"""


def walk(page: Page, base: str, furnished, before=None) -> dict[str, str]:  # noqa: F811
    failures: dict[str, str] = {}
    paths = signed_in_paths(
        furnished["application"],
        furnished["company"],
        furnished["applicant"],
        furnished["experience"],
        things=furnished,
    )
    for path in paths:
        page.goto(f"{base}{path}")
        if "reauthenticate" in page.url:
            page.locator("input[name=password]").fill(PASSWORD)
            page.locator("form").get_by_role("button").first.click()
            page.goto(f"{base}{path}")
        if before:
            before(page)
        for clipped in page.evaluate(CLIPPED):
            failures.setdefault(
                f"words {clipped['needs']}px {clipped['axis']} in a box of {clipped['box']}px  "
                f"{clipped['what']}",
                path,
            )
        result = page.evaluate(SCROLLS_SIDEWAYS)
        for culprit in result["culprits"]:
            failures.setdefault(
                f"{culprit['width']}px wide, {culprit['over']}px over  {culprit['what']}", path
            )
        if result["reached"] and not result["culprits"]:
            failures.setdefault(f"scrolls {result['reached']}px sideways", path)
    return failures


def report(failures: dict[str, str]) -> str:
    return "\n".join(f"  {where}\n    {what}" for what, where in sorted(failures.items()))


def test_nothing_is_lost_under_the_text_spacing_override(page: Page, live_server, furnished):  # noqa: F811
    base = live_server.url
    sign_in(page, base)
    page.set_viewport_size({"width": 1280, "height": 900})

    failures = walk(page, base, furnished, before=lambda p: p.add_style_tag(content=TEXT_SPACING))

    assert not failures, (
        f"{len(failures)} box(es) clip their words or push the page sideways under the text "
        f"spacing override (WCAG 2.2 SC 1.4.12):\n{report(failures)}"
    )


def test_nothing_is_lost_at_two_hundred_percent_zoom(page: Page, live_server, furnished):  # noqa: F811
    """A 640-pixel layout viewport is what 200% zoom leaves a 1280-pixel window with. The
    320-pixel reflow pass is 400%; a table may scroll in its box there and here alike, and
    what may not happen is a box clipping its own words."""
    base = live_server.url
    sign_in(page, base)
    page.set_viewport_size({"width": ZOOMED, "height": 450})

    failures = walk(page, base, furnished)

    assert not failures, (
        f"{len(failures)} box(es) clip their words or push the page sideways at 200% zoom:\n"
        f"{report(failures)}"
    )


def test_less_motion_means_no_motion(page: Page, live_server, furnished):  # noqa: F811
    """The accessibility page says the little animation there is respects
    `prefers-reduced-motion`; this keeps that a tested sentence."""
    base = live_server.url
    sign_in(page, base)
    page.emulate_media(reduced_motion="reduce")
    page.goto(f"{base}/jobs/companies/")

    durations = page.evaluate(
        """() => [...document.querySelectorAll('.btn, .htmx-indicator, [aria-busy], a')]
            .slice(0, 40)
            .map(el => getComputedStyle(el).transitionDuration)
            .filter(d => d && d !== '0s')"""
    )
    # `0.01ms`, which Chromium reports in seconds.
    assert set(durations) <= {"0.01ms", "1e-05s"}, durations
