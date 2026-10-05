"""Every page, opened as few times as the instruments allow (#722).

Ten tests used to walk the whole of `signed_in_paths()` -- some 150 addresses -- each on its
own: axe in light and again in dark, text spacing, 200% zoom, target size, the
`aria-describedby` check, target size again with the interface compact, and the reflow pass
in three languages. Fifteen hundred page loads, and the slowest tests in the suite.

Four of those walks look at the same page in the same state, so here they share one load.
At 1280 by 900, in light, each page is asked, in this order:

1. **`aria-describedby`** (#114), which only reads the document;
2. **axe** at WCAG 2.2 A and AA (`test_accessibility.py` says what it does and does not
   cover), except on a preview, which is a print document (`AXE_EXEMPT_SUFFIXES`);
3. **target size** (#115), the masthead's menus on the first page and the first few of each
   page's own, opened one at a time and closed again;
4. **text spacing** (SC 1.4.12) last, because its constructed stylesheet stays on the page
   and there is no instrument after it to spoil.

Each instrument keeps its own findings and its own message, so a failure says which one
found what, as the separate tests did. An instrument that breaks on a page -- an evaluate
error, a menu with no panel -- is recorded as a finding of that instrument on that page and
the walk goes on; a page that never arrives ends the walk but not its report, and whatever
the four instruments found before it is reported beside the error. So one instrument's
crash never hides another's findings, as it could not when they were separate tests.

**Why dark and 200% zoom are walks of their own.** The plan was one load per page for those
too: switch the colour scheme in place and back, and narrow the window to 640 and widen it
again. Measured on 5 October 2026, in one process beside the old walks, that was slower than
the ten walks it replaced, 150 seconds to 141. A page load from the live server is a tenth
of a second; what costs is drawing the page, and a page switched to dark or resized is drawn
again in full either way. Switched in place it is worse: `app.css` fades colours with
`transition-colors`, so before axe can read the dark colours every transition has to be run
to its end, and a resize has to wait out the frames in which `app.js` answers it (its resize
handlers and ResizeObserver work in the next frame). Loading the page already dark, or
already 640 wide, has neither problem and draws it once. So those two are walks of their
own, as they were -- three tests rather than one, which xdist can also spread.

The walks whose page is drawn differently for other reasons stay where they were: target
size with the interface compact (`test_target_size.py`), and reflow at 320 pixels in each
of three languages (`test_reflow.py`).
"""

from __future__ import annotations

import traceback
from collections.abc import Callable, Iterator
from contextlib import contextmanager

import pytest
from playwright.sync_api import Page

from .conftest import PASSWORD
from .test_accessibility import (  # noqa: F401 - `furnished` and `axe_source` are fixtures
    axe_should_read,
    axe_source,
    describe,
    furnished,
    sign_in,
    signed_in_paths,
    violations_on,
)
from .test_described_by import dangling_on
from .test_reflow import goto_watched
from .test_target_size import MINIMUM, measure_targets
from .test_text_spacing import ADOPT, TEXT_SPACING, ZOOMED, lost_on, report

pytestmark = pytest.mark.e2e

#: The window the walks are taken in.
WIDE = {"width": 1280, "height": 900}


def every_page(
    page: Page,
    base: str,
    furnished,  # noqa: F811
    *,
    skip: Callable[[str], bool] = lambda path: False,
) -> Iterator[str]:
    """Open every page of the signed-in walk in turn, yielding each path once it is open.

    Signs in first. A path `skip` answers true for is passed over without being opened. A
    page that sends the person to confirm their password again gets it, and is opened again.
    Each load goes through `goto_watched`, so a page that never arrives
    says what this process was doing meanwhile (#721).
    """
    sign_in(page, base)
    seen: list[tuple[str, float]] = []
    for path in signed_in_paths(
        furnished["application"],
        furnished["company"],
        furnished["applicant"],
        furnished["experience"],
        things=furnished,
    ):
        if skip(path):
            continue
        goto_watched(page, f"{base}{path}", seen)
        if "reauthenticate" in page.url:
            page.locator("input[name=password]").fill(PASSWORD)
            page.locator("form").get_by_role("button").first.click()
            page.goto(f"{base}{path}")
        yield path


def broke(error: Exception, where: str, *, first: bool) -> str:
    """A line for an instrument that raised on `where`, with the whole traceback the first time."""
    if first:
        return f"{where} raised:\n" + "".join(traceback.format_exception(error)).rstrip()
    return f"{where} raised {type(error).__name__}: {error}".rstrip()


@contextmanager
def recorded(crashes: dict[str, list[str]], instrument: str, path: str) -> Iterator[None]:
    """Run one instrument on one page; if it raises, keep the error as its finding and go on."""
    try:
        yield
    except Exception as error:  # reported in the instrument's own section
        found = crashes.setdefault(instrument, [])
        found.append(broke(error, path, first=not found))


def test_every_page_in_light_with_every_instrument(
    live_server,
    page: Page,
    axe_source,  # noqa: F811
    furnished,  # noqa: F811
):
    page.set_viewport_size(WIDE)

    dangling: dict[str, str] = {}
    axe: list[str] = []
    targets: dict[str, str] = {}
    spaced: dict[str, str] = {}
    crashes: dict[str, list[str]] = {}
    stopped = ""
    path = "signing in"
    try:
        for index, path in enumerate(every_page(page, live_server.url, furnished)):
            with recorded(crashes, "aria-describedby", path):
                for problem in dangling_on(page):
                    dangling.setdefault(problem, path)

            if axe_should_read(path):
                with recorded(crashes, "axe", path):
                    found = violations_on(page, axe_source)
                    if found:
                        axe.append(describe(f"{path} (light)", found))

            with recorded(crashes, "target size", path):
                # The masthead's menus are the same on every page: measured on the first.
                measure_targets(page, path, targets, masthead=index == 0)

            with recorded(crashes, "text spacing", path):
                page.evaluate(ADOPT, TEXT_SPACING)
                for what in lost_on(page):
                    spaced.setdefault(what, path)
    except Exception as error:  # reported below, with what came before
        stopped = broke(error, f"after {path}, the walk", first=True)

    sections = []
    if stopped:
        sections.append(
            "walk: stopped before the last page, so what follows is only what was found "
            f"before it:\n{stopped}"
        )
    for instrument, lines in crashes.items():
        sections.append(
            f"{instrument}: raised on {len(lines)} page(s), which it could not check:\n"
            + "\n".join(lines)
        )
    if dangling:
        sections.append(
            f"aria-describedby: {len(dangling)} broken reference(s):\n{report(dangling)}"
        )
    if axe:
        sections.append("axe:\n" + "\n\n".join(axe))
    if targets:
        sections.append(
            f"target size: {len(targets)} target(s) under {MINIMUM}x{MINIMUM} with no "
            f"exception (WCAG 2.2 SC 2.5.8):\n{report(targets)}"
        )
    if spaced:
        sections.append(
            f"text spacing: {len(spaced)} box(es) clip their words or push the page sideways "
            f"under the text spacing override (WCAG 2.2 SC 1.4.12):\n{report(spaced)}"
        )
    assert not sections, "\n\n\n".join(sections)


def test_every_page_in_dark_has_no_violations(
    live_server,
    page: Page,
    axe_source,  # noqa: F811
    furnished,  # noqa: F811
):
    page.emulate_media(color_scheme="dark")
    page.set_viewport_size(WIDE)

    failures = []
    for path in every_page(
        page, live_server.url, furnished, skip=lambda path: not axe_should_read(path)
    ):
        found = violations_on(page, axe_source)
        if found:
            failures.append(describe(f"{path} (dark)", found))
    assert not failures, "\n\n".join(failures)


def test_nothing_is_lost_at_two_hundred_percent_zoom(
    live_server,
    page: Page,
    furnished,  # noqa: F811
):
    """A 640-pixel layout viewport is what 200% zoom leaves a 1280-pixel window with. The
    320-pixel reflow pass is 400%; a table may scroll in its box there and here alike, and
    what may not happen is a box clipping its own words."""
    page.set_viewport_size({"width": ZOOMED, "height": 450})

    failures: dict[str, str] = {}
    for path in every_page(page, live_server.url, furnished):
        for what in lost_on(page):
            failures.setdefault(what, path)

    assert not failures, (
        f"{len(failures)} box(es) clip their words or push the page sideways at 200% zoom:\n"
        f"{report(failures)}"
    )
