"""Does anything scroll sideways at 320 pixels? WCAG 2.2 SC 1.4.10 Reflow (#113).

Reflow is level AA and its number is 320 CSS pixels: the width a 1280-pixel window has
after 400% zoom, which is how somebody with low vision reads a page. At that width content
may scroll down, and must not scroll across, because reading a line that runs off the side
means finding the start of the next one by hand, every line.

axe-core does not check this, and it is not axe failing: reflow is a question about layout
at a width, and axe is asked about a document. The suite next door also runs at the default
viewport, so until this test nothing had ever looked at these pages narrow. Every one of
them scrolled sideways, by an identical 331 pixels -- the tell that it was one element and
not thirteen pages.

The failure names the element rather than the page, because "/applications/ overflows by
331" sends you looking through a template and `nav.flex` at 651 wide does not.

**What is allowed to scroll.** Content that needs two dimensions to mean anything -- a data
table, a diagram -- is exempt by the criterion itself, and Postulo puts those in their own
`overflow-x: auto` box. That box scrolls; the page does not. So an element inside something
that scrolls on purpose is not reported, and an element that pushes the document itself
wider is.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from .conftest import PASSWORD
from .test_accessibility import furnished, sign_in, signed_in_paths  # noqa: F401

pytestmark = pytest.mark.e2e

#: The criterion's width. 400% zoom on a 1280-pixel window lands here.
NARROW = 320

SCROLLS_SIDEWAYS = r"""() => {
  const limit = document.documentElement.clientWidth;

  // The criterion's actual question, asked of the browser rather than inferred from
  // scrollWidth: try to scroll the page sideways, and see how far it goes. scrollWidth
  // says yes to things that never scroll -- an sr-only box clipped to nothing still
  // counts towards it -- and this does not.
  const before = window.scrollX;
  window.scrollTo(10000, 0);
  const reached = Math.round(window.scrollX);
  window.scrollTo(before, 0);
  if (reached === 0) return {reached: 0, culprits: []};

  // Which element started it. A box inside something that scrolls on purpose is not the
  // cause -- unless it is absolutely positioned and that ancestor is not its containing
  // block, in which case it escapes the scroll box and takes the page with it.
  const contained = (el) => {
    const positioned = getComputedStyle(el).position === 'absolute';
    for (let p = el.parentElement; p && p !== document.documentElement; p = p.parentElement) {
      const style = getComputedStyle(p);
      if (style.overflowX === 'visible') continue;
      if (positioned && style.position === 'static') continue;
      return true;
    }
    return false;
  };
  const over = (el) => el.getBoundingClientRect().right > limit + 1;

  const describe = (el) => {
    const r = el.getBoundingClientRect();
    return {
      width: Math.round(r.width),
      over: Math.round(r.right - limit),
      what: el.outerHTML.replace(/\s+/g, ' ').slice(0, 120),
    };
  };

  // What is actually out there, asked of the browser rather than worked out from boxes.
  // Every element's border box can sit inside the viewport and the page still scroll --
  // a margin, a transform and an outline all add scrollable overflow that
  // `getBoundingClientRect` does not show. So scroll to the far right and ask what is
  // under the last column of pixels.
  const outThere = () => {
    const found = new Map();
    const was = window.scrollX;
    window.scrollTo(10000, 0);
    const x = document.documentElement.clientWidth - 2;
    for (let y = 4; y < window.innerHeight; y += 12) {
      for (const el of document.elementsFromPoint(x, y)) {
        if (el === document.body || el === document.documentElement) continue;
        const key = el.tagName + (el.className || '');
        if (!found.has(key)) {
          const r = el.getBoundingClientRect();
          const style = getComputedStyle(el);
          found.set(key, {
            what: el.outerHTML.replace(/\s+/g, ' ').slice(0, 110),
            right: Math.round(r.right),
            marginRight: style.marginRight,
            transform: style.transform === 'none' ? '' : style.transform,
            position: style.position,
          });
        }
      }
    }
    window.scrollTo(was, 0);
    return [...found.values()].slice(0, 8);
  };

  const out = [];
  // Everything over the edge, whatever the filters below decide about it. When the filters
  // agree that nothing is to blame and the page scrolls anyway, this is what gets reported
  // instead -- because "something overflows and I cannot say what" is not a bug report.
  const everything = [];
  for (const el of document.querySelectorAll('body *')) {
    const r = el.getBoundingClientRect();
    if (r.width === 0 || !over(el)) continue;
    everything.push({
      ...describe(el),
      position: getComputedStyle(el).position,
      contained: contained(el),
      parentOver: !!(el.parentElement && over(el.parentElement)),
    });
    if (contained(el)) continue;
    // Report the one that starts it, not everything it carries along.
    if (el.parentElement && over(el.parentElement) && !contained(el.parentElement)) continue;
    out.push(describe(el));
  }
  return {
    reached,
    culprits: out,
    everything: everything.slice(0, 12),
    outThere: out.length ? [] : outThere(),
    metrics: {
      clientWidth: limit,
      innerWidth: window.innerWidth,
      docScrollWidth: document.documentElement.scrollWidth,
      bodyScrollWidth: document.body.scrollWidth,
    },
  };
}"""


def test_no_page_scrolls_sideways_at_320_pixels(live_server, page: Page, furnished):  # noqa: F811
    base = live_server.url
    sign_in(page, base)
    page.set_viewport_size({"width": NARROW, "height": 800})

    paths = signed_in_paths(furnished["application"], furnished["company"], furnished["applicant"])
    failures: dict[str, str] = {}
    for path in paths:
        page.goto(f"{base}{path}")
        if "reauthenticate" in page.url:
            page.locator("input[name=password]").fill(PASSWORD)
            page.locator("form").get_by_role("button").first.click()
            page.goto(f"{base}{path}")

        result = page.evaluate(SCROLLS_SIDEWAYS)
        if not result["reached"]:
            continue
        for culprit in result["culprits"]:
            key = f"{culprit['width']}px wide, {culprit['over']}px over  {culprit['what']}"
            failures.setdefault(key, path)
        if not result["culprits"]:
            # The filters found nothing to blame and the page scrolls regardless, so show
            # the working: the page's own measurements, what is actually sitting in the
            # overflowed column, and everything over the edge with the reason it was ruled
            # out. A border box inside the viewport can still scroll the page -- a margin,
            # a transform and an outline all add scrollable overflow that a rectangle does
            # not show -- so the middle one is the question the others cannot answer.
            detail = (
                f"      metrics: {result['metrics']}\n"
                + "".join(
                    f"      at the far edge: right={row['right']} "
                    f"margin-right={row['marginRight']} position={row['position']} "
                    f"transform={row['transform'] or 'none'}\n        {row['what']}\n"
                    for row in result["outThere"]
                )
                + "\n".join(
                    f"      {row['width']}x, {row['over']}px over, {row['position']}, "
                    f"contained={row['contained']} parentOver={row['parentOver']}\n"
                    f"        {row['what']}"
                    for row in result["everything"]
                )
            )
            failures.setdefault(
                f"scrolls {result['reached']}px sideways; nothing passed the filters. "
                f"Over the edge:\n{detail}",
                path,
            )

    report = "\n".join(f"  {where}\n    {what}" for what, where in sorted(failures.items()))
    assert not failures, (
        f"{len(failures)} element(s) push the page sideways at {NARROW}px "
        f"(WCAG 2.2 SC 1.4.10 Reflow):\n{report}"
    )


def test_the_navigation_becomes_a_menu_and_still_works(live_server, page: Page, furnished):  # noqa: F811
    """The header's row of links is a disclosure below 768 pixels, and has to work as one.

    Six links come to 635 pixels, so on a phone they are behind a button. That button is the
    only way to reach most of the application at that width, which makes it worth a test of
    its own: it opens from the keyboard, closes with Escape, lists the same places, and one
    of them takes you there. Exactly one of the two navigations is in the layout at a time,
    so the duplicate links never reach the accessibility tree.
    """
    base = live_server.url
    sign_in(page, base)
    page.goto(f"{base}/")

    row = page.locator('header nav[class~="md:flex"]')
    button = page.locator("header summary", has_text="Menu")

    page.set_viewport_size({"width": 1280, "height": 900})
    expect(row).to_be_visible()
    expect(button).to_be_hidden()

    page.set_viewport_size({"width": NARROW, "height": 800})
    expect(row).to_be_hidden()
    expect(button).to_be_visible()

    button.focus()
    page.keyboard.press("Enter")
    expect(page.locator("header details[data-menu][open]")).to_have_count(1)
    page.keyboard.press("Escape")
    expect(page.locator("header details[data-menu][open]")).to_have_count(0)

    page.keyboard.press("Enter")
    panel = page.locator("header details[data-menu][open] nav")
    panel.get_by_role("link", name="Companies").click()
    expect(page).to_have_url(f"{base}/jobs/companies/")
