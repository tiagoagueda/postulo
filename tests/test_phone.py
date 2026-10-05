"""What a phone gets that a desktop does not (#73).

The browser suite walks every page at a phone's width (`tests/e2e/test_every_page.py`); what
is held here is the source of the decisions it cannot see: the coarse-pointer sizes, the
board's one-column answer and the keyboards the fields ask for.
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
    assert block.count("2.75rem") >= 4
    for selector in (".tap-target", '.btn[data-size="xs"]', "select", "textarea"):
        assert selector in block
    # 16 pixels in a text box, or a phone's browser zooms in on it and stays zoomed.
    assert "font-size: 1rem" in block


def test_the_board_snaps_to_a_column_on_a_phone():
    match = re.search(r"@media \(width < 40rem\) \{\s*\.board-box \{(.*?)\}", CSS, re.S)
    assert match and "scroll-snap-type: x proximity" in match.group(1)
    assert "[data-board-section]" in CSS and "scroll-snap-align: start" in CSS


def test_an_open_column_is_nearly_the_window_wide_on_a_phone_and_288_from_sm():
    assert "w-[calc(100vw-4rem)] sm:w-72" in BOARD


def test_the_fields_ask_for_the_keyboard_that_suits_them():
    form = PostingIntakeForm()
    assert 'type="url"' in str(form["url"])
    assert 'type="date"' in str(form["closes_at"])
