"""The bar under each language on *Server settings -> Defaults*, in a browser (#312).

The sums are held in `tests/test_translation_bar.py`. What only a browser can say is what
the drawing does with them: that the page draws one per language and stays clean for axe
in both themes, that sixty-odd bars fit a 320-pixel screen without anything scrolling
sideways, and that under a high-contrast theme the three parts are still three.
"""

from __future__ import annotations

from itertools import pairwise

import pytest
from playwright.sync_api import Page

from .test_accessibility import axe_source, describe, sign_in, violations_on  # noqa: F401
from .test_reflow import NARROW, SCROLLS_SIDEWAYS, SPILLS

pytestmark = pytest.mark.e2e

#: Every part in play for one language, so there is something to tell apart.
MIXED = {"total": 1873, "translated": 1500, "drafts": 688, "reviewed": 812, "percent": 80}


@pytest.fixture
def administrator(applicant):
    """The page is the administrator's."""
    applicant.is_staff = True
    applicant.is_superuser = True
    applicant.save(update_fields=["is_staff", "is_superuser"])
    return applicant


ROWS_AND_BARS = """() => {
  const rows = [...document.querySelectorAll('label')].filter(
    (label) => label.querySelector('input[name=offered_languages]'));
  return {
    rows: rows.length,
    withBar: rows.filter((row) => row.querySelectorAll('svg.translation-bar').length === 1).length,
    drawn: rows.filter((row) => {
      const bar = row.querySelector('svg.translation-bar');
      if (!bar) return false;
      const box = bar.getBoundingClientRect();
      return box.width > 0 && box.height > 0;
    }).length,
  };
}"""


def parts_of(page: Page, code: str) -> list[dict]:
    """Each drawn part of one language's bar: what it is, its paint, and where it sits."""
    return page.evaluate(
        """(code) => {
            const row = document.querySelector(`input[name=offered_languages][value="${code}"]`)
              .closest('label');
            return [...row.querySelectorAll('svg.translation-bar rect')].map((rect) => {
              const box = rect.getBoundingClientRect();
              return {
                part: rect.dataset.part,
                fill: getComputedStyle(rect).fill,
                left: box.left,
                right: box.right,
              };
            });
        }""",
        code,
    )


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_every_language_has_its_bar_and_the_page_stays_clean(
    page: Page,
    live_server,
    administrator,
    axe_source,  # noqa: F811
    scheme,
):
    page.emulate_media(color_scheme=scheme)
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/server/defaults/")

    counted = page.evaluate(ROWS_AND_BARS)

    assert counted["rows"] > 30, counted
    assert counted["withBar"] == counted["rows"], counted
    assert counted["drawn"] == counted["rows"], counted
    found = violations_on(page, axe_source)
    assert not found, describe(f"/server/defaults/ ({scheme})", found)


def test_the_bars_reflow_at_320_pixels(page: Page, live_server, administrator):
    sign_in(page, live_server.url)
    page.set_viewport_size({"width": NARROW, "height": 800})
    page.goto(f"{live_server.url}/server/defaults/")

    assert page.evaluate(ROWS_AND_BARS)["drawn"] > 30, "the page, with its bars"
    assert page.evaluate(SCROLLS_SIDEWAYS)["reached"] == 0
    assert page.evaluate(SPILLS) == []
    too_wide = page.evaluate(
        """() => [...document.querySelectorAll('svg.translation-bar')].filter((bar) => {
            const row = bar.closest('label').getBoundingClientRect();
            const box = bar.getBoundingClientRect();
            return box.left < row.left || box.right > row.right;
        }).length"""
    )
    assert too_wide == 0


def test_the_parts_are_painted_apart_and_never_touch(
    page: Page, live_server, administrator, monkeypatch
):
    from postulo.core import languages

    shipped = languages.translation_status()
    monkeypatch.setattr(languages, "translation_status", lambda: {**shipped, "de": MIXED})
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/server/defaults/")

    parts = parts_of(page, "de")

    assert [p["part"] for p in parts] == ["reviewed", "draft", "untranslated"]
    assert len({p["fill"] for p in parts}) == 3, parts
    assert "rgb(0, 0, 0)" not in {p["fill"] for p in parts}, "an SVG's default, unpainted"
    for before, after in pairwise(parts):
        assert after["left"] - before["right"] >= 1, f"{before['part']} touches {after['part']}"


def test_the_parts_still_differ_under_forced_colours(
    page: Page, live_server, administrator, monkeypatch
):
    """A high-contrast theme repaints what a page chose, and three fills repainted to one
    colour would leave a bar with nothing but gaps in it. The bar opts out and takes three
    system colours instead."""
    from postulo.core import languages

    shipped = languages.translation_status()
    monkeypatch.setattr(languages, "translation_status", lambda: {**shipped, "de": MIXED})
    sign_in(page, live_server.url)
    page.emulate_media(forced_colors="active")
    page.goto(f"{live_server.url}/server/defaults/")

    parts = parts_of(page, "de")
    adjust = page.evaluate(
        "() => getComputedStyle(document.querySelector('svg.translation-bar')).forcedColorAdjust"
    )

    assert adjust == "none"
    assert len({p["fill"] for p in parts}) == 3, parts
    for before, after in pairwise(parts):
        assert after["left"] - before["right"] >= 1, f"{before['part']} touches {after['part']}"
