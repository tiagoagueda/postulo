"""What a phone gets that a desktop does not (#73).

The browser suite walks every page at a phone's width (`tests/e2e/test_every_page.py`); what
is held here is the source of the decisions it cannot see: the coarse-pointer sizes, the
board's stacked sections and the keyboards the fields ask for.
"""

from __future__ import annotations

import re
from pathlib import Path

from postulo.applications.forms import PostingIntakeForm

ROOT = Path(__file__).resolve().parent.parent
CSS = (ROOT / "assets" / "css" / "app.css").read_text(encoding="utf-8")
BOARD = (
    ROOT / "src" / "postulo" / "templates" / "applications" / "partials" / "board.html"
).read_text(encoding="utf-8")


def coarse_block() -> str:
    match = re.search(r"@media \(pointer: coarse\) \{(.*?)\n  \}\n", CSS, re.S)
    assert match, "no coarse-pointer rules"
    return match.group(1)


def test_a_coarse_pointer_gets_forty_four_pixels_not_a_narrow_window():
    block = coarse_block()
    assert block.count("2.75rem") >= 3
    for selector in (".tap-target", "td[data-actions]", "select", "textarea"):
        assert selector in block
    # `.btn` is Basecoat's name, so its half is painted in the style pack, not in app.css.
    pack = (ROOT / "assets" / "css" / "basecoat.css").read_text(encoding="utf-8")
    coarse = "".join(re.findall(r"@media \(pointer: coarse\) \{(.*?)\n\}\n", pack, re.S))
    assert '.btn[data-size="xs"]' in coarse
    assert coarse.count("2.75rem") >= 2
    # 16 pixels in a text box, or a phone's browser zooms in on it and stays zoomed.
    assert "font-size: 1rem" in block


def test_the_board_is_stacked_on_a_phone_and_the_page_scrolls_it():
    """Below `sm` the sections are one column the page's width, and the box is no box: it
    scrolled both ways and snapped to a column, on a screen too small for a box (#844)."""
    match = re.search(r"@media \(width < 40rem\) \{\s*\.board-box \{(.*?)\}", CSS, re.S)
    assert match, "no phone rule for the board's box"
    assert "overflow: visible" in match.group(1) and "max-block-size: none" in match.group(1)
    for phone in ("max-sm:grid-flow-row", "max-sm:auto-cols-auto", "max-sm:justify-stretch"):
        assert phone in BOARD, phone
    # The heading sticks under the masthead, and a strip's name is written across.
    assert "max-sm:top-[var(--header-height,4.25rem)]" in BOARD
    assert "sm:[writing-mode:vertical-rl]" in BOARD
    assert " [writing-mode:vertical-rl]" not in BOARD


def test_an_open_column_is_the_page_wide_on_a_phone_and_288_from_sm():
    assert "{% if column.open %}sm:w-72{% else %}" in BOARD
    assert "100vw" not in BOARD


def test_the_fields_ask_for_the_keyboard_that_suits_them():
    form = PostingIntakeForm()
    assert 'type="url"' in str(form["url"])
    assert 'type="date"' in str(form["closes_at"])
