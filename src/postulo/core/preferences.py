"""A person's own time zone and language, put in force for what is being done for them.

Two places know whom a request is for, and they learn it at different moments. A page
knows from the session, before any view runs, and `UserPreferencesMiddleware` acts on it.
A call made with an API token knows only once the token has been read, which is inside the
view: nothing is signed in, so the middleware saw nobody and the request carried on in
whatever language its ``Accept-Language`` named -- English for a script that sends none,
the browser's for an extension -- and in the instance's time zone. A refusal's sentence,
and the words an interview booked through the API writes on a timeline, came out in a
language nobody had chosen (#393).

So the rule lives here, once, and both of them call it.
"""

from __future__ import annotations

import zoneinfo

from django.utils import timezone

from . import languages


def apply(profile, request=None) -> None:
    """Activate ``profile``'s time zone and language for the rest of this request.

    Without a profile the zone is the instance's and the language is left as it stands,
    which is whatever `LocaleMiddleware` took from the request.

    The zone is set on every call, rather than only when a profile supplies one. Workers
    are reused across requests, and a time zone left activated by the previous visitor
    would otherwise be inherited by the next.
    """
    # The person's own zone, else the instance default an administrator may have set,
    # else what the environment says (which deactivate() falls back to).
    tz_name = (getattr(profile, "time_zone", "") if profile else "") or instance_zone()
    try:
        timezone.activate(zoneinfo.ZoneInfo(tz_name))
    except (zoneinfo.ZoneInfoNotFoundError, ValueError):
        # A profile holding a time zone this machine does not know should not take
        # the whole request down; fall back to the instance default.
        timezone.deactivate()

    # A language an administrator has stopped offering is not applied, and the stored
    # value is left exactly where it is: withdrawing a language must not silently
    # rewrite a hundred people's settings, because it may be offered again tomorrow.
    # Asked about the language it will be drawn in, which is Postulo's own nearest: a
    # profile holding `sr` from before the list said which script reads `sr-Cyrl`, and
    # that is the one an instance offers or does not (#337).
    language = languages.catalogue(getattr(profile, "language", "") if profile else "")
    if language and not offered(language):
        language = ""
    if language:
        languages.activate(language)
        if request is not None:
            request.LANGUAGE_CODE = languages.current()


def offered(code: str) -> bool:
    """Whether the instance still offers this language."""
    from . import site

    try:
        return site.offers(code)
    except Exception:  # pragma: no cover - a broken settings row must not blank a page
        return True


def instance_zone() -> str:
    """The instance default, or the environment's if the database cannot be asked.

    Reading it is a query, and this runs in front of `/healthz` -- whose answer only
    matters on the day the database is the thing that is broken. Letting the query take the
    request down turns the 503 that probe exists to return into a 500, which reports "the
    application is down" where it should report "the database is".
    """
    from django.conf import settings

    from . import site

    try:
        return site.default_time_zone()
    except Exception:
        return settings.TIME_ZONE
