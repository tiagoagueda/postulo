"""Arranging the dashboard, on the dashboard (#201).

Pressing *Arrange* puts the page itself into an editing mode: the widgets stay where they
are and grow the controls to move them, take them off, and add the ones not shown. The
separate page under Settings that did this -- a list of names, arranged blind and checked
by switching back -- is gone; the effect of a move is seen where it happens.

**The mode is a moment, not a preference.** It is a parameter on the dashboard's own
address, ``/?arrange=1``, so it survives a reload, behaves under the back button, and is
left by a plain link. Nothing remembers that somebody was arranging.

Two rules carry over from the page this replaces, and both were fought for (#124, #125):

* **Every action works with scripts off.** Buttons that post, a redirect back into the
  mode with a fragment so focus lands on the widget that moved, and a sentence saying
  where it landed. Dragging is an addition to that, never a replacement -- it does not
  fire on a touch screen and is not reachable from a keyboard.
* **One dimension, not two.** Widgets have widths and fill rows in order, so there is no
  cell to name: *left* and *right* move one place, *up* and *down* a whole row, and on a
  narrow screen the two axes coincide. Seeing the widget move in front of you makes this
  easier to read, not harder; the four buttons stay the model.

The data is untouched: ``Profile.dashboard_widgets`` and ``dashboard_known``, read and
written through :mod:`postulo.core.widgets` as before.
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.views import redirect_to_login
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.translation import gettext as _

from . import widgets

#: The query parameter that opens the mode. Its presence is what counts, not its value.
PARAM = "arrange"


def wanted(request: HttpRequest) -> bool:
    """Whether this request asks for the dashboard in its editing mode."""
    return PARAM in request.GET


def mode_url(fragment: str = "") -> str:
    """The dashboard in its mode, with a fragment when focus has somewhere to go."""
    return f"{reverse('core:home')}?{PARAM}=1{fragment}"


def profile_for(user):
    """The person's profile, seeded with the standard arrangement if it was never made."""
    from postulo.accounts.models import Profile

    profile, created = Profile.objects.get_or_create(user=user)
    if created:
        widgets.seed(profile)
        profile.save(update_fields=["dashboard_widgets", "dashboard_known", "updated_at"])
    return profile


def context_for(profile, page: list[widgets.Rendered]) -> dict:
    """What the mode adds to the page: which moves would act, and what is not shown.

    Each rendered widget learns which of the four directions would change anything, so a
    button that cannot act says so rather than posting and doing nothing (#124). Widgets
    this account has never been offered are shown apart and first: a release or a plugin
    added them, and they wait rather than walking onto the page (#123).
    """
    keys = widgets.keys_for(profile)
    for item in page:
        item.moves = {way: widgets.can_move(keys, item.spec.key, way) for way in widgets.DIRECTIONS}
    fresh = [widget for widget in widgets.new_for(profile) if widget.key not in keys]
    return {
        "fresh": fresh,
        "available": [
            (group, [w for w in items if w.key not in keys and w not in fresh])
            for group, items in widgets.groups()
        ],
        "is_standard": widgets.is_standard(profile),
    }


def apply(request: HttpRequest) -> HttpResponse:
    """One action posted to the dashboard: add, remove, dismiss, a move, a drop, or reset.

    Every one redirects back into the mode, because the person is still arranging. A move
    carries a fragment so focus follows the widget, and a message says which row and place
    it landed in -- a move that happens in silence is a move somebody using a screen reader
    has to go looking for.
    """
    if not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path())
    profile = profile_for(request.user)
    keys = widgets.keys_for(profile)
    action = request.POST.get("action", "")
    key = request.POST.get("key", "")

    if action == "reset":
        # This account's own copy of the standard arrangement, not an absence of one: reset
        # means "give me the standard page", and it is still this account's (#123).
        widgets.seed(profile)
        profile.save(update_fields=["dashboard_widgets", "dashboard_known", "updated_at"])
        return redirect(mode_url())
    if key not in widgets.REGISTRY:
        messages.error(request, _("That is not a widget Postulo knows about."))
        return redirect(mode_url())
    # "dismiss" changes nothing about the page and everything about the offer: a new widget
    # somebody said no to stops being new without going on.
    if action != "dismiss":
        keys = rearrange(keys, action, key, request.POST)

    profile.dashboard_widgets = keys
    # Whatever was just acted on is decided about now, however it was decided.
    profile.dashboard_known = sorted(widgets.known_to(profile) | {key})
    profile.save(update_fields=["dashboard_widgets", "dashboard_known", "updated_at"])

    if (action in widgets.DIRECTIONS or action == "place") and key in keys:
        say_where_it_landed(request, keys, key)
        return redirect(mode_url(f"#widget-{key}"))
    return redirect(mode_url())


def rearrange(keys: list[str], action: str, key: str, data=None) -> list[str]:
    keys = list(keys)
    if action == "add":
        if key not in keys:
            keys.append(key)
    elif action == "remove":
        keys = [k for k in keys if k != key]
    elif action in widgets.DIRECTIONS:
        keys = widgets.move(keys, key, action)
    elif action == "place":
        # Where a drop lands. The same list the arrows edit, so dragging is an addition to
        # the control that works everywhere rather than a second way of storing anything
        # (#125).
        keys = widgets.place(keys, key, data.get("to", "") if data is not None else "")
    return keys


def say_where_it_landed(request: HttpRequest, keys: list[str], key: str) -> None:
    """Row and place rather than a direction: it is what somebody actually wants to know, it
    is the same sentence for all four buttons, and it is two numbers because the control is
    two-dimensional.
    """
    rows = widgets.rows_of(keys)
    row = widgets.row_of(keys, key)
    widget = widgets.get(key)
    messages.success(
        request,
        _("%(name)s is now in row %(row)s, place %(place)s.")
        % {"name": widget.called, "row": row + 1, "place": rows[row].index(key) + 1},
    )
