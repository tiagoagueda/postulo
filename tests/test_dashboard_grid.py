"""The dashboard as a grid a person arranges, and by dragging as well (#125).

> the disposition can be flexible in a 4 or 5 x something grid, widgets organized by
> drag-drop, for now using builting widget (internal widgets plugin) but later can be
> extended (future release)

Third of three, and the two below it decided most of what this could be: #123 settled where
an arrangement is stored, #124 settled how a widget is placed by somebody not using a mouse.
What is left is the grid itself, the gesture, and three questions the issue said should be
settled before anything was built on top of them — the column count, holes and overlaps, and
what a phone does with a wide-screen arrangement.
"""

from __future__ import annotations

from pathlib import Path

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


# --------------------------------------------------------------- four, not five


def test_the_grid_is_four_columns():
    """Five divides by nothing useful: a half-width widget in five columns is two columns
    or three and never half, and every widget declares its width in exactly those words.
    """
    template = Path("src/postulo/templates/core/dashboard.html").read_text(encoding="utf-8")

    assert "lg:grid-cols-4" in template
    assert "lg:grid-cols-12" not in template


def test_each_width_is_a_whole_number_of_columns():
    template = Path("src/postulo/templates/core/dashboard.html").read_text(encoding="utf-8")

    for span in ("lg:col-span-1", "lg:col-span-2", "lg:col-span-4"):
        assert span in template


def test_the_spans_are_written_out_rather_than_assembled():
    """Tailwind reads the templates, and a class it never sees is a class it never compiles
    — silently, which is the failure mode worth guarding against.
    """
    template = Path("src/postulo/templates/core/dashboard.html").read_text(encoding="utf-8")

    assert "col-span-{{" not in template
    css = Path("src/postulo/static/css/app.css").read_text(encoding="utf-8")
    for span in ("col-span-1", "col-span-2", "col-span-4"):
        assert span in css, f"{span} reached the compiled stylesheet"


def test_the_widths_did_not_have_to_be_renamed():
    """Four takes a quarter, a half and a whole with no arithmetic and no renaming, which
    is the reason it is four.
    """
    assert widgets.WIDTHS == ("quarter", "half", "full")
    assert widgets.UNITS == {"quarter": 3, "half": 6, "full": 12}


# ------------------------------------------------------- packed, so neither can happen


def test_two_widgets_cannot_share_a_cell(user):
    """A coordinate model admits that and a gap in the middle of a page; this one cannot
    represent either, so none of the code that would repair them exists.
    """
    arrange(user, ["counters", "gone_quiet", "upcoming_interviews"])
    profile = Profile.objects.get(user=user)

    rows = widgets.rows_of(widgets.keys_for(profile))

    everywhere = [key for row in rows for key in row]
    assert len(everywhere) == len(set(everywhere))


def test_a_widget_that_is_gone_leaves_no_hole(user):
    """Uninstalling a plugin is a normal event. The rest close up, because a place in the
    order is all a widget has.
    """
    arrange(user, ["counters", "acme:gone", "gone_quiet"])
    profile = Profile.objects.get(user=user)

    assert widgets.keys_for(profile) == ["counters", "gone_quiet"]
    assert widgets.rows_of(widgets.keys_for(profile)) == [["counters"], ["gone_quiet"]]


def test_an_arrangement_is_still_an_ordered_list(user):
    """A place in the order and a width; the row falls out of the two. That is the whole
    model, and it is the one that needs no validating.
    """
    arrange(user, ["counters", "gone_quiet"])

    assert order(user) == ["counters", "gone_quiet"]


# ------------------------------------------------------------------ and on a phone


def test_a_narrow_screen_is_one_column_in_the_order_it_was_arranged():
    """#73 is open on the phone and this does not assume it solved: what a phone gets is
    the same arrangement read downwards, which is the only reading that needs no second
    answer.
    """
    template = Path("src/postulo/templates/core/dashboard.html").read_text(encoding="utf-8")

    assert "grid-cols-1 gap-6 lg:grid-cols-4" in template
    assert "col-span" in template.split("lg:grid-cols-4")[1]
    assert "sm:col-span" not in template, "no second answer for a middle size"


def test_the_grid_names_no_side_of_the_page():
    """Column one is the start edge, and grid placement is where that is easiest to get
    wrong.
    """
    import re

    template = Path("src/postulo/templates/core/dashboard.html").read_text(encoding="utf-8")

    assert not re.search(r"\b(col-start|ml|mr|pl|pr|left|right)-\d", template)


# ----------------------------------------------------------- dropping one into place


def test_a_drop_puts_a_widget_where_it_landed(client, user):
    arrange(user, ["counters", "gone_quiet", "upcoming_interviews"])
    client.force_login(user)

    client.post(reverse(ARRANGE), {"action": "place", "key": "counters", "to": "2"})

    assert order(user) == ["gone_quiet", "upcoming_interviews", "counters"]


def test_a_drop_past_the_end_means_last(client, user):
    """Somebody meaning *last*, not somebody making a mistake."""
    arrange(user, ["counters", "gone_quiet"])
    client.force_login(user)

    client.post(reverse(ARRANGE), {"action": "place", "key": "counters", "to": "99"})

    assert order(user) == ["gone_quiet", "counters"]


def test_a_position_that_is_not_a_number_changes_nothing(client, user):
    arrange(user, ["counters", "gone_quiet"])
    client.force_login(user)

    client.post(reverse(ARRANGE), {"action": "place", "key": "counters", "to": "somewhere"})

    assert order(user) == ["counters", "gone_quiet"]


def test_a_drop_says_where_it_landed(client, user):
    """The same sentence a keyboard move gets, because it is the same move."""
    arrange(user, ["counters", "gone_quiet", "upcoming_interviews"])
    client.force_login(user)

    html = client.post(
        reverse(ARRANGE), {"action": "place", "key": "counters", "to": "2"}, follow=True
    ).content.decode()

    assert 'role="status"' in html
    assert "row" in html and "place" in html


def test_a_drop_posts_where_the_arrows_post(client, user):
    """No new endpoint and no second store: a page arranged by dragging and a page arranged
    by pressing arrows are the same page, saved the same way.
    """
    client.force_login(user)

    html = client.get(reverse(ARRANGE)).content.decode()

    assert f'data-widget-place="{reverse(ARRANGE)}"' in html


def test_nothing_is_draggable_without_a_script(client, user):
    """An affordance that does nothing is worse than none, and the four arrows are the
    control that works everywhere.
    """
    client.force_login(user)

    html = client.get(reverse(ARRANGE)).content.decode()

    assert "draggable" not in html
    assert "data-widget-row" in html, "but the script is told what to make draggable"


def test_the_arrows_are_still_there(client, user):
    """Dragging is an addition to the control that works everywhere, never a replacement."""
    client.force_login(user)

    html = client.get(reverse(ARRANGE)).content.decode()

    for way in widgets.DIRECTIONS:
        assert f'value="{way}"' in html


# ----------------------------------------------- what "internal widgets plugin" means


def test_widgets_are_a_registry_named_as_a_plugin_rather_than_a_plugin_kind():
    """The cheap answer, and the one the issue itself thought right for this release.

    Making a widget a plugin kind would put seventeen rows in *Server settings → Plugins*
    for an administrator to switch off, which is a wall rather than an improvement, and
    would need an entry-point contract before anybody outside is asking for one. What the
    contract actually needs is a **key that cannot collide**, and that already exists.
    """
    from postulo.plugins.registry import GROUPS

    assert "widget" not in GROUPS

    with pytest.raises(ValueError, match="needs a key beginning"):
        widgets.register(
            widgets.Widget(
                key="mine",
                label="x",
                blurb="x",
                template="core/widgets/shortcuts.html",
                context=lambda sources: {},
                provider="acme",
            )
        )


def test_the_shared_pass_is_still_shared():
    """Several widgets exist only because the expensive answer is computed once, and a grid
    must not end up building each of them in isolation.
    """
    source = Path("src/postulo/core/widgets.py").read_text(encoding="utf-8")

    assert "class Sources" in source
    assert "sources = Sources(" in source or "Sources(" in source
