"""Placing a widget without a mouse, in two dimensions (#124).

> widgets organized by drag-drop

A prerequisite rather than a refinement. `static/js/app.js` states the rule every drag in
this application obeys — *drag and drop does not fire on touch screens and is not reachable
from a keyboard, so it is an addition to the control that works everywhere, never a
replacement for it* — and that makes the control the thing that has to exist first.

**One dimension is not two, and the dashboard is a flow rather than a matrix.** Widgets have
widths and fill rows in order, so there is no cell to name: that rules out a row-and-column
picker, and rules out a "move this one, then choose a destination" mode, which would need
two interactions and state between them to work with scripts off. What is left is four
directions over the order — one place either way, one whole row either way — and every one
of them is a form that posts.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import ClassVar

import pytest
from django.urls import reverse

from postulo.accounts.models import Profile
from postulo.core import widgets

pytestmark = pytest.mark.django_db

ARRANGE = "settings:dashboard"


def arrange(user, keys: list[str]) -> None:
    Profile.objects.filter(user=user).update(dashboard_widgets=keys)


def order(user) -> list[str]:
    return Profile.objects.get(user=user).dashboard_widgets


# ----------------------------------------------------------------- the shape of a page


def test_widths_decide_what_shares_a_row():
    """A row takes widgets in order until the next one would not fit — the same twelve
    columns the template draws with.
    """
    # counters is full; gone_quiet and upcoming_interviews are halves.
    rows = widgets.rows_of(["counters", "gone_quiet", "upcoming_interviews", "due_reminders"])

    assert rows == [
        ["counters"],
        ["gone_quiet", "upcoming_interviews"],
        ["due_reminders"],
    ]


def test_the_row_is_computed_rather_than_stored():
    """So it stays true the day somebody changes a widget's width."""
    source = Path("src/postulo/core/widgets.py").read_text(encoding="utf-8")

    assert "def rows_of" in source
    assert "dashboard_rows" not in source, "no second copy of the layout to keep in step"


# ---------------------------------------------------------------- the four directions


def test_left_and_right_move_one_place():
    keys = ["a_full", "b_half", "c_half"]
    with a_registry(keys):
        assert widgets.move(keys, "c_half", "left") == ["a_full", "c_half", "b_half"]
        assert widgets.move(keys, "a_full", "right") == ["b_half", "a_full", "c_half"]


def test_up_and_down_move_a_whole_row():
    """The direction a list could not express, which is the whole of this issue."""
    keys = ["a_full", "b_half", "c_half", "d_full"]
    with a_registry(keys):
        # b and c share a row; moving b down puts it past the whole of d's row.
        assert widgets.rows_of(keys) == [["a_full"], ["b_half", "c_half"], ["d_full"]]
        assert widgets.move(keys, "b_half", "down") == ["a_full", "c_half", "d_full", "b_half"]
        # And up puts it in front of the row above.
        assert widgets.move(keys, "d_full", "up") == ["a_full", "d_full", "b_half", "c_half"]


def test_the_edges_are_no_ops_rather_than_wraps():
    keys = ["a_full", "b_half", "c_half"]
    with a_registry(keys):
        assert widgets.move(keys, "a_full", "up") == keys
        assert widgets.move(keys, "a_full", "left") == keys
        assert widgets.move(keys, "c_half", "down") == keys
        assert widgets.move(keys, "c_half", "right") == keys


def test_a_direction_that_cannot_act_says_so_before_it_is_pressed():
    keys = ["a_full", "b_half", "c_half"]
    with a_registry(keys):
        assert widgets.can_move(keys, "a_full", "up") is False
        assert widgets.can_move(keys, "a_full", "down") is True
        assert widgets.can_move(keys, "b_half", "right") is True


def test_a_key_that_is_not_there_moves_nowhere():
    assert widgets.move(["counters"], "nothing", "up") == ["counters"]
    assert widgets.move(["counters"], "counters", "sideways") == ["counters"]


# -------------------------------------------------------------- and through the page


def test_every_direction_is_a_form_that_posts(client, user):
    """Scripts off has to keep working, or the dashboard becomes the first part of Postulo
    that cannot be arranged without them.
    """
    client.force_login(user)

    html = client.get(reverse(ARRANGE)).content.decode()

    for way in widgets.DIRECTIONS:
        assert html.count(f'<button type="submit" name="action" value="{way}"') >= 1
    # Nothing in the arranging list is draggable, and nothing in it needs htmx: the header's
    # theme switch does, and is not what this page is for.
    body = html.split("<main")[-1]
    assert "draggable" not in body
    assert "hx-" not in body


def test_moving_a_widget_through_the_page(client, user):
    arrange(user, ["counters", "gone_quiet", "upcoming_interviews"])
    client.force_login(user)

    client.post(reverse(ARRANGE), {"action": "down", "key": "counters"})

    assert order(user) == ["gone_quiet", "upcoming_interviews", "counters"]


def test_focus_follows_the_widget(client, user):
    """A move that happens at the top of the page is a move somebody using a keyboard has
    to go looking for. The fragment takes them to it.
    """
    arrange(user, ["counters", "gone_quiet"])
    client.force_login(user)

    response = client.post(reverse(ARRANGE), {"action": "down", "key": "counters"})

    assert response["Location"].endswith("#widget-counters")


def test_the_row_the_fragment_lands_on_can_take_focus(client, user):
    client.force_login(user)

    html = client.get(reverse(ARRANGE)).content.decode()

    assert 'id="widget-counters" tabindex="-1"' in html


def test_a_move_says_where_it_landed(client, user):
    """Row and place rather than a direction: two numbers, because the control is
    two-dimensional, and the same sentence for all four buttons.
    """
    arrange(user, ["counters", "gone_quiet", "upcoming_interviews"])
    client.force_login(user)

    response = client.post(reverse(ARRANGE), {"action": "down", "key": "counters"}, follow=True)
    html = response.content.decode()

    assert 'role="status"' in html
    assert re.search(r"row\s*2,\s*place\s*1", html), html[:0] or "no position was announced"


def test_a_button_that_cannot_act_is_disabled_rather_than_missing(client, user):
    """The cluster keeps its shape, so the arrow somebody reaches for is where it was."""
    arrange(user, ["counters", "gone_quiet"])
    client.force_login(user)

    html = client.get(reverse(ARRANGE)).content.decode()
    first = html.split('id="widget-counters"')[1].split("</li>")[0]

    assert first.count("disabled") >= 2, "up and left cannot act on the first widget"
    assert 'value="down"' in first


# ------------------------------------------------------------------ and what axe walks


def test_each_arrow_has_a_name(client, user):
    """A drag handle that is a bare element with a cursor is a finding, not a detail —
    and so is an icon-only button with nothing to read out.
    """
    client.force_login(user)

    html = client.get(reverse(ARRANGE)).content.decode()

    for phrase in ("up a row", "down a row", "one place earlier", "one place later"):
        assert phrase in html


def test_the_arrows_flip_for_right_to_left():
    """An arrow meaning "one place earlier" that points at the far margin in Arabic is
    worse than no arrow. The stylesheet already mirrors this pair; this is what says the
    two new icons joined the set that gets mirrored.
    """
    css = Path("assets/css/app.css").read_text(encoding="utf-8")
    listed = Path("assets/icons.txt").read_text(encoding="utf-8")

    for name in ("arrow-left", "arrow-right"):
        assert name in listed
        assert f'[data-icon="{name}"]' in css


def test_the_four_are_written_down_once():
    """The view, the template and this file all mean the same four."""
    assert widgets.DIRECTIONS == ("up", "down", "left", "right")


# ------------------------------------------------------------------------- helpers


class a_registry:
    """Widgets of known widths, registered for the length of a test and gone after.

    Named rather than real widgets, so a test about the layout does not fail the day
    somebody changes what `counters` spans.
    """

    WIDTHS: ClassVar[dict[str, str]] = {
        "a_full": "full",
        "b_half": "half",
        "c_half": "half",
        "d_full": "full",
    }

    def __init__(self, keys: list[str]) -> None:
        self.keys = keys

    def __enter__(self):
        for key in self.keys:
            widgets.register(
                widgets.Widget(
                    key=key,
                    label=key,
                    blurb="",
                    template="core/widgets/shortcuts.html",
                    context=lambda sources: {},
                    width=self.WIDTHS[key],
                )
            )
        return self

    def __exit__(self, *exc) -> None:
        for key in self.keys:
            widgets.REGISTRY.pop(key, None)
