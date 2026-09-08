"""Django's admin, mounted only when asked for and rate-limited when it is (#116).

Postulo has its own *Server settings* — people, sign-in policy, plugins, email, logs,
defaults — so the admin is a developer's convenience rather than something the application
needs. On a self-hosted instance it is mostly a second, less careful way into the same data,
and it used to sit at ``/admin/`` on every instance whose operator had not read one line of
the settings file.

Two things follow, and only the second one lives here.

**It is not mounted unless an operator says so.** ``POSTULO_ADMIN_URL`` is empty by default
and ``config/urls.py`` adds nothing when it is. Choosing to run the admin and choosing where
it lives are then the same decision, made once, on purpose.

**When it is mounted, its login is throttled.** allauth's rate limits are good ones and
Postulo inherits them, but they apply to allauth's views. ``django.contrib.admin`` has a
login view of its own and nothing was limiting it — so the one credential form on the
instance with no attempt limiting was the one that reaches every table directly. The limit
below is allauth's own, through allauth's own limiter and cache, so there are not two
schemes to keep in step: ``ACCOUNT_RATE_LIMITS["admin_login"]`` is where it is configured
and it defaults to the same ``10/m/ip, 5/300s/key`` as a failed sign-in.
"""

from __future__ import annotations

from allauth.core import ratelimit
from django.contrib.admin.apps import AdminConfig
from django.contrib.admin.sites import AdminSite


class ThrottledAdminSite(AdminSite):
    """The admin, with its login held to the same limits as Postulo's own."""

    def login(self, request, extra_context=None):
        # Only a POST is an attempt; allauth's limiter ignores GET for the same reason, so
        # loading the form as often as you like costs nothing and guessing does.
        if request.method == "POST":
            refusal = ratelimit.consume_or_429(
                request,
                action="admin_login",
                # The username being guessed against, so a thousand attempts on one account
                # are counted together however many addresses they come from. Truncated
                # because the key goes into a cache key and the field is not length-checked
                # until the form validates, which is after this.
                key=(request.POST.get("username") or "")[:150],
            )
            if refusal is not None:
                return refusal
        return super().login(request, extra_context)


class PostuloAdminConfig(AdminConfig):
    """Replaces ``django.contrib.admin`` in ``INSTALLED_APPS`` to install the site above."""

    default_site = "postulo.core.admin_site.ThrottledAdminSite"
