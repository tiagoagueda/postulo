"""Request-scoped preferences for the signed-in person, and the answer htmx understands."""

from __future__ import annotations

import zoneinfo
from urllib.parse import urlsplit

from django.conf import settings
from django.http import HttpResponse
from django.shortcuts import resolve_url
from django.urls import NoReverseMatch, reverse
from django.utils import timezone, translation

#: Statuses that are a redirect and carry a ``Location``. 307 and 308 are here for
#: completeness; nothing in Postulo answers with either.
REDIRECTS = frozenset({301, 302, 303, 307, 308})


class HtmxLoginRedirectMiddleware:
    """Send an htmx request that has outlived its session to the sign-in page.

    A browser's ``XMLHttpRequest`` follows a redirect without telling the script it
    happened, so htmx never sees the 302 that says *sign in first*: it sees the 200 the
    sign-in page answers with and does what it was told to do with a 200, which is to swap
    it into the target. Leave a filtered table open over lunch, touch a filter, and the
    whole sign-in page -- masthead, footer and a form asking for a password -- appeared
    inside ``#applications-table``, on a page that still looked signed in (#226).

    ``HX-Redirect`` is the header htmx reads before it looks at anything else, so the
    redirect is turned into one and the browser goes to the sign-in page as a page. The
    address is passed through whole, ``?next=`` and all, so signing in comes back to the
    table that was being filtered.

    **Only the sign-in page.** Every other redirect an htmx request meets is one a view
    meant, and swapping what it leads to is what those views are written to expect; turning
    all of them into full page loads would undo the swapping this application is built on.
    So this recognises one destination and leaves the rest alone.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if getattr(request, "htmx", None) and response.status_code in REDIRECTS:
            location = response.headers.get("Location", "")
            # Relative only. What htmx does with this header is set `location.href`, so an
            # address naming a host would be this middleware handing a browser to somewhere
            # else because a path matched. Django's sign-in redirect has never been
            # anything but relative, which is what makes the rule free.
            parts = urlsplit(location)
            if location and not parts.netloc and not parts.scheme:
                if parts.path in self._sign_in_paths():
                    return self._tell_htmx(response, location)
        return response

    @staticmethod
    def _tell_htmx(response: HttpResponse, location: str) -> HttpResponse:
        """A 204 carrying the address, keeping everything else the redirect had.

        Cookies and headers are copied rather than dropped: a session cycled or a message
        stored on the way out belongs to this reply as much as the ``Location`` did.
        """
        answer = HttpResponse(status=204)
        for header, value in response.headers.items():
            if header.lower() not in {"location", "content-type", "content-length"}:
                answer.headers[header] = value
        answer.cookies = response.cookies
        answer.headers["HX-Redirect"] = location
        return answer

    @staticmethod
    def _sign_in_paths() -> set[str]:
        """Where *sign in first* points, by both names it is reached under.

        ``LOGIN_URL`` is what Django's own ``LoginRequiredMixin`` resolves, and
        ``account_login`` is what allauth reverses; they are the same page today and
        nothing guarantees they stay one setting apart.
        """
        paths = set()
        try:
            paths.add(urlsplit(resolve_url(settings.LOGIN_URL)).path)
        except NoReverseMatch:  # pragma: no cover - a misconfigured LOGIN_URL
            pass
        try:
            paths.add(reverse("account_login"))
        except NoReverseMatch:  # pragma: no cover - allauth is always mounted
            pass
        return paths


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
        # The instance's policy row is memoised per request (#231): a dozen little questions
        # read it and each used to be its own query. This is the request boundary that makes
        # "per request" true -- a worker thread answers thousands of them and must not hold
        # an administrator's settings from an hour ago.
        from postulo.plugins import policy

        from . import site

        site.forget_current()
        # Likewise for "is this plugin on for this person", which a page asks once per mark
        # it draws and which reads a file and a table to answer (#231).
        policy.forget_decisions()

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

        # A language an administrator has stopped offering is not applied, and the stored
        # value is left exactly where it is: withdrawing a language must not silently
        # rewrite a hundred people's settings, because it may be offered again tomorrow.
        language = getattr(profile, "language", "") if profile else ""
        if language and not self._offered(language):
            language = ""
        if language:
            translation.activate(language)
            request.LANGUAGE_CODE = translation.get_language()

        return self.get_response(request)

    @staticmethod
    def _offered(code: str) -> bool:
        from . import site

        try:
            return site.offers(code)
        except Exception:  # pragma: no cover - a broken settings row must not blank a page
            return True

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
