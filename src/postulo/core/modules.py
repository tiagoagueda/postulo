"""Asking whether a module is on, from a view, a template or a list (#877).

A module that adds a file format -- iCalendar, vCard, Word, OpenDocument -- decides whether
that format is *offered*: its download, its import, its feed. The decision is the plugin
policy's, and these are the three ways the rest of the application asks it, so that a view
that forgets to ask is the exception a test finds and not the way things are written.

**Off is a 404 for a signed-in person, and the login page for a stranger.** An address that
answered "this is switched off" would tell anyone which modules an instance runs; a 404 says
only that there is nothing there, which is what is true. Existing subscriptions to a feed
therefore stop answering while the module is off and answer again, at the same address,
when it is on.
"""

from __future__ import annotations

from functools import wraps

from django.http import Http404


def is_on(user, name: str) -> bool:
    """Whether the module called ``name`` is on for this person (an unknown name is on)."""
    from postulo.plugins import policy
    from postulo.plugins.registry import find_any

    if find_any(name) is None:
        return True
    return policy.decide(name, user).on


def required(name: str):
    """Decorate a view callable, or a ``Class.as_view()``: 404 while the module is off."""

    def decorate(view):
        @wraps(view)
        def guarded(request, *args, **kwargs):
            user = getattr(request, "user", None)
            if user is not None and user.is_authenticated and not is_on(user, name):
                raise Http404
            return view(request, *args, **kwargs)

        return guarded

    return decorate
