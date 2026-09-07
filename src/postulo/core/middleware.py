"""Request-scoped preferences for the signed-in person."""

from __future__ import annotations

import zoneinfo

from django.utils import timezone, translation


class UserPreferencesMiddleware:
    """Activate the signed-in person's time zone and language for the request.

    Must run after ``AuthenticationMiddleware``, since it needs ``request.user``, and
    after ``LocaleMiddleware``, whose choice it deliberately overrides: an explicit
    preference stored on a profile beats a browser header.

    Both settings are reset on every request rather than only when a profile supplies
    one. Workers are reused across requests, and a time zone left activated by the
    previous visitor would otherwise be inherited by the next.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        profile = self._profile(request)

        # The person's own zone, else the instance default an administrator may have set,
        # else what the environment says (which deactivate() falls back to).
        tz_name = (getattr(profile, "time_zone", "") if profile else "") or self._instance_zone()
        try:
            timezone.activate(zoneinfo.ZoneInfo(tz_name))
        except (zoneinfo.ZoneInfoNotFoundError, ValueError):
            # A profile holding a time zone this machine does not know should not take
            # the whole request down; fall back to the instance default.
            timezone.deactivate()

        language = getattr(profile, "language", "") if profile else ""
        if language:
            translation.activate(language)
            request.LANGUAGE_CODE = translation.get_language()

        return self.get_response(request)

    @staticmethod
    def _instance_zone() -> str:
        """The instance default, or the environment's if the database cannot be asked.

        Reading it is a query, and this middleware runs in front of `/healthz` -- whose
        answer only matters on the day the database is the thing that is broken. Letting the
        query take the request down turns the 503 that probe exists to return into a 500,
        which reports "the application is down" where it should report "the database is".
        """
        from django.conf import settings

        from . import site

        try:
            return site.default_time_zone()
        except Exception:
            return settings.TIME_ZONE

    @staticmethod
    def _profile(request):
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            return None
        # Missing profiles are possible for rows created before the signal existed,
        # or by a fixture; they should degrade to instance defaults, not an error.
        return getattr(user, "profile", None)
