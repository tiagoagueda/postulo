"""Is everything you can click at least 24 by 24? Measured, not read (#115).

Target Size (Minimum) is SC 2.5.8, new at AA in WCAG 2.2. axe-core does not enforce it —
its `target-size` rule reports "needs review" rather than a violation — so the suite next
door was reporting a clean bill of health on pages carrying sixty-two buttons that were
22 pixels square. Two under, sixty-two times, and nothing said so.

Reading the markup would not have found them either: `p-1` around a `size-3.5` icon is
4 + 14 + 4, and nobody adds that up while writing a template. So this measures, in a real
browser, and holds the boxes to the criterion in Python where the arithmetic can be read.

What counts as passing:

* **24 by 24.** The plain case.
* **Spacing.** Under 24, a target still passes if a 24-pixel circle centred on it touches
  nothing else clickable — which is the criterion's own wording, and why two short links
  on the dashboard are fine where eight in a column were not.
* **The label, for a checkbox or a radio.** Those are 13 by 13 and the browser decides
  that, not Postulo. The thing a pointer aims at is the label that switches them, so the
  label is what gets measured.
* **Inline.** A link inside a sentence is exempt, because its height is the line's and
  the line belongs to the prose around it. A link that is the only thing on its line is
  not in a sentence and gets no exemption — that distinction is the whole reason the
  dashboard's shortcut column failed and a link in a paragraph does not.

What this cannot judge is the *equivalent* exception — "another control on the page does
the same job at full size" — which needs a person. Nothing here relies on it; if something
ever does, it belongs in `EXEMPT` with the reason written down rather than in a filter.

One viewport, 1280 by 900. Narrow layouts rearrange enough to deserve their own pass, and
that belongs with the reflow work rather than here.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import pytest
from playwright.sync_api import Page

from .conftest import PASSWORD
from .test_accessibility import furnished, sign_in, signed_in_paths  # noqa: F401

pytestmark = pytest.mark.e2e

#: The criterion's number, in CSS pixels, and the radius of the circle the spacing
#: exception draws on an undersized target.
MINIMUM = 24
RADIUS = MINIMUM / 2

#: Targets excused by hand, each with the reason. Matched against the description below.
#: Keep this empty if you can; every entry is a promise somebody checked.
EXEMPT: dict[str, str] = {}

#: How many menus to open per page, one at a time. A page's row menus are the same menu
#: repeated, so the first few cover the markup; opening all of them would only be slower.
MENUS_PER_PAGE = 3

COLLECT = """() => {
  const CLICKABLE = [
    'a[href]', 'button', 'input:not([type=hidden])', 'select', 'textarea', 'summary',
    '[tabindex]:not([tabindex="-1"])',
  ].join(', ');

  // A checkbox is 13x13 whatever anybody does. The target is the label that switches it.
  const switchedBy = (el) => {
    if (el.tagName !== 'INPUT') return null;
    if (el.type !== 'checkbox' && el.type !== 'radio') return null;
    const wrapping = el.closest('label');
    if (wrapping) return wrapping;
    return el.id ? document.querySelector('label[for="' + CSS.escape(el.id) + '"]') : null;
  };

  // "In a sentence": laid out inline, with prose beside it in the same block. An
  // inline-block or inline-flex box is not constrained by anybody's line-height, and a
  // button is never in a sentence, so neither qualifies.
  const inASentence = (el) => {
    if (el.tagName !== 'A') return false;
    if (getComputedStyle(el).display !== 'inline') return false;
    const parent = el.parentElement;
    if (!parent) return false;
    return [...parent.childNodes].some(
      (n) => n !== el && n.nodeType === Node.TEXT_NODE && n.textContent.trim()
    );
  };

  const out = [];
  for (const el of document.querySelectorAll(CLICKABLE)) {
    if (el.disabled) continue;
    const style = getComputedStyle(el);
    if (style.visibility === 'hidden' || style.pointerEvents === 'none') continue;
    // Clipped to nothing: screen-reader-only, and it gets a real box when focused.
    if (style.clip === 'rect(0px, 0px, 0px, 0px)') continue;

    let box = el.getBoundingClientRect();
    const label = switchedBy(el);
    if (label) {
      const lb = label.getBoundingClientRect();
      if (lb.width * lb.height > box.width * box.height) box = lb;
    }
    if (box.width < 1 || box.height < 1) continue;

    out.push({
      what: el.outerHTML.replace(/\\s+/g, ' ').slice(0, 130),
      x: box.x, y: box.y, w: box.width, h: box.height,
      inline: inASentence(el),
    });
  }
  return out;
}"""


@dataclass(frozen=True)
class Target:
    what: str
    x: float
    y: float
    w: float
    h: float
    inline: bool

    @property
    def undersized(self) -> bool:
        return self.w < MINIMUM or self.h < MINIMUM

    @property
    def centre(self) -> tuple[float, float]:
        return self.x + self.w / 2, self.y + self.h / 2

    def gap_from(self, px: float, py: float) -> float:
        """How far a point is from this box: zero if it is inside."""
        dx = max(self.x - px, 0.0, px - (self.x + self.w))
        dy = max(self.y - py, 0.0, py - (self.y + self.h))
        return math.hypot(dx, dy)


def spaced_apart(target: Target, everything: list[Target]) -> bool:
    """The spacing exception, as worded: a 24-pixel circle on the target, hitting nothing.

    Against another undersized target it is circle against circle, so the centres must be
    a full 24 apart. Against one that meets the size it is circle against box, so the box
    must clear the centre by the radius. Touching is not intersecting, hence the strict
    comparisons.
    """
    cx, cy = target.centre
    for other in everything:
        if other is target:
            continue
        if other.undersized:
            ox, oy = other.centre
            if math.hypot(cx - ox, cy - oy) < MINIMUM:
                return False
        elif other.gap_from(cx, cy) < RADIUS:
            return False
    return True


def too_small(page: Page) -> list[str]:
    """Every target on the page as it stands that meets neither the size nor an exception."""
    targets = [Target(**row) for row in page.evaluate(COLLECT)]
    failures = []
    for target in targets:
        if not target.undersized or target.inline:
            continue
        if any(mark in target.what for mark in EXEMPT):
            continue
        if spaced_apart(target, targets):
            continue
        failures.append(f"{round(target.w)}x{round(target.h)}  {target.what}")
    return failures


def test_everything_clickable_is_big_enough_to_hit(live_server, page: Page, furnished):  # noqa: F811
    base = live_server.url
    sign_in(page, base)
    page.set_viewport_size({"width": 1280, "height": 900})

    paths = signed_in_paths(furnished["application"], furnished["company"], furnished["applicant"])
    found: dict[str, str] = {}
    for path in paths:
        page.goto(f"{base}{path}")
        if "reauthenticate" in page.url:
            page.locator("input[name=password]").fill(PASSWORD)
            page.locator("form").get_by_role("button").first.click()
            page.goto(f"{base}{path}")

        for failure in too_small(page):
            found.setdefault(failure, path)

        # A closed <details> has no boxes to measure, and the column chooser -- which is
        # what #115 was about -- lives inside one. Open them one at a time: two menus open
        # at once is not a state anybody reaches, and their boxes would overlap.
        menus = page.locator("details[data-menu]")
        for index in range(min(menus.count(), MENUS_PER_PAGE)):
            page.evaluate(
                "i => { document.querySelectorAll('details[data-menu]')[i].open = true }", index
            )
            for failure in too_small(page):
                found.setdefault(failure, f"{path} (menu {index + 1})")
            page.evaluate(
                "i => { document.querySelectorAll('details[data-menu]')[i].open = false }", index
            )

    report = "\n".join(f"  {where}\n    {what}" for what, where in sorted(found.items()))
    assert not found, (
        f"{len(found)} target(s) under {MINIMUM}x{MINIMUM} with no exception "
        f"(WCAG 2.2 SC 2.5.8):\n{report}"
    )
