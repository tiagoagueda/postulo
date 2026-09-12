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

**Words that run out of their box.** The other way to lose content at this width does not
scroll at all. A row of words beside a group of buttons that will not give way leaves the
words whatever is over -- on the arrange page, a column one word wide, with the longest
written across the arrows beside it, and on the career page fourteen pixels in Greek. The
page is exactly as wide as the screen and half of it cannot be read, so the walk also asks
every box holding text whether its words fit inside it.

**In which languages.** A page is made of translated words, and a word that cannot break is as
wide as its language makes it. The walk used to be English only, which is how the arrange page
reached CI fitting in English with seven pixels to spare on one machine and eight over on
another, while in Greek it was 64 over on any machine at all (#165).
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from .conftest import PASSWORD
from .test_accessibility import furnished, sign_in, signed_in_paths  # noqa: F401

pytestmark = pytest.mark.e2e

#: The criterion's width. 400% zoom on a 1280-pixel window lands here.
NARROW = 320

#: The languages the walk is taken in: the one the interface is written in, and the two that
#: drew widest of the European catalogues where they were measured -- Greek, whose letters are
#: wider, and German, whose compounds do not break. Both overflowed pages that fit in English
#: (#165). A catalogue that draws wider still belongs on this list.
LANGUAGES = ("en", "el", "de")

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

  // Which element is responsible, established by removing things rather than by reasoning
  // about boxes. Every rectangle can sit inside the viewport and the page still scroll: a
  // margin, a transform and an outline all add scrollable overflow that
  // `getBoundingClientRect` never shows, and two attempts to deduce the culprit from
  // rectangles both reported the settings sidebar -- which is on every settings page,
  // while only one of them scrolls.
  //
  // So: hide a child, ask whether the page still overflows, put it back. If hiding it
  // fixed the page, the cause is inside it, and the same question is asked of its
  // children. This ends at the smallest element that is actually to blame and cannot be
  // fooled by anything, because it never looks at a box at all.
  const blame = () => {
    const wide = () => document.documentElement.scrollWidth > limit;
    if (!wide()) return [];
    const trail = [];
    let culprit = document.body;
    for (let depth = 0; depth < 40; depth++) {
      let descended = false;
      for (const child of [...culprit.children]) {
        const was = child.style.display;
        child.style.display = 'none';
        const fixed = !wide();
        child.style.display = was;
        if (fixed) {
          culprit = child;
          trail.push(describe(child));
          descended = true;
          break;
        }
      }
      if (!descended) break;
    }
    // The last entry is the smallest element whose removal fixes the page; the ones before
    // it are the path down to it, which is what says where to look.
    return trail.slice(-4);
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
    blame: out.length ? [] : blame(),
    metrics: {
      clientWidth: limit,
      innerWidth: window.innerWidth,
      docScrollWidth: document.documentElement.scrollWidth,
      bodyScrollWidth: document.body.scrollWidth,
    },
  };
}"""

#: Every box that holds words and is narrower than they are: its text runs out over whatever
#: sits beside it. Only boxes that let it show -- one that clips or scrolls on purpose, like
#: `truncate` or a table's own scroll box, is doing what it was told -- and only boxes, since
#: an inline run of text has no width of its own to be too narrow for.
SPILLS = r"""() => {
  const out = [];
  for (const el of document.querySelectorAll('body *')) {
    // Drawn at all: the inside of a closed <details> is still in the tree, with boxes of no
    // meaning, and only the browser can say it is not on the screen.
    if (!el.checkVisibility({visibilityProperty: true})) continue;
    const style = getComputedStyle(el);
    if (style.display === 'inline' || style.display === 'contents') continue;
    if (style.overflowX !== 'visible') continue;
    if (el.clientWidth <= 1) continue;
    const words = [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim());
    if (!words) continue;
    if (el.scrollWidth > el.clientWidth + 1) {
      out.push({
        box: el.clientWidth,
        needs: el.scrollWidth,
        what: el.outerHTML.replace(/\s+/g, ' ').slice(0, 120),
      });
    }
  }
  return out;
}"""


@pytest.mark.parametrize("language", LANGUAGES)
def test_no_page_scrolls_sideways_at_320_pixels(
    live_server,
    page: Page,
    furnished,  # noqa: F811
    language,
):
    from postulo.accounts.models import Profile

    base = live_server.url
    sign_in(page, base)
    # The account's own setting, the way a person would choose it, rather than the browser's:
    # it is what every page after signing in is drawn in.
    Profile.objects.filter(user=furnished["applicant"]).update(language=language)
    page.set_viewport_size({"width": NARROW, "height": 800})

    paths = signed_in_paths(
        furnished["application"],
        furnished["company"],
        furnished["applicant"],
        furnished["experience"],
        things=furnished,
    )
    failures: dict[str, str] = {}
    for path in paths:
        page.goto(f"{base}{path}")
        if "reauthenticate" in page.url:
            page.locator("input[name=password]").fill(PASSWORD)
            page.locator("form").get_by_role("button").first.click()
            page.goto(f"{base}{path}")

        for spill in page.evaluate(SPILLS):
            failures.setdefault(
                f"words {spill['needs']}px wide in a box of {spill['box']}px  {spill['what']}",
                path,
            )

        result = page.evaluate(SCROLLS_SIDEWAYS)
        if not result["reached"]:
            continue
        for culprit in result["culprits"]:
            key = f"{culprit['width']}px wide, {culprit['over']}px over  {culprit['what']}"
            failures.setdefault(key, path)
        if not result["culprits"]:
            # The filters found nothing to blame and the page scrolls regardless, so show
            # the working: the page's own measurements, the element whose removal actually
            # fixes it, and everything over the edge with the reason it was ruled out. The
            # middle one is the answer; the other two are for reading it against, because
            # a rectangle inside the viewport can still scroll the page and twice now the
            # rectangles have pointed at something that was not to blame.
            detail = (
                f"      metrics: {result['metrics']}\n"
                + "".join(
                    f"      hiding this fixes the page: {row['width']}px wide, "
                    f"{row['over']}px over\n        {row['what']}\n"
                    for row in result["blame"]
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
        f"{len(failures)} element(s) push the page sideways, or run out of their own box, "
        f"at {NARROW}px in {language!r} (WCAG 2.2 SC 1.4.10 Reflow):\n{report}"
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
