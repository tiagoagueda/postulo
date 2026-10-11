"""Asking for a recent sign-in, including of the people who have no password (#741).

allauth's `reauthentication_required` asks somebody to prove themselves again before a
destructive step -- deleting the account, taking a backup, making a recovery link. It can only
ask for what a person has: a password, or a second factor. An account that signs in through
the identity provider alone has neither, and allauth lets it through at once ("for now, this is
not implemented"), so a hijacked single-sign-on session could delete the account it was
borrowed from.

**What this does for them is the one thing left to ask: that they signed in a moment ago.**
Within allauth's reauthentication timeout, as everyone else's proof is; otherwise they are
signed out, told why, and sent to the sign-in page with the page they wanted as the place to
come back to. A fresh round trip to the provider is the proof, and it is the provider's.

Everything else is allauth's: a person with a password or a second factor is asked for it,
by allauth, exactly as before.
"""

from __future__ import annotations

import time
from functools import wraps

from allauth.account import app_settings
from allauth.account.authentication import get_authentication_records
from allauth.account.decorators import reauthentication_required as allauth_requires
from allauth.account.internal.flows.reauthentication import get_reauthentication_flows
from django.contrib import messages
from django.contrib.auth import REDIRECT_FIELD_NAME, logout
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.http import urlencode
from django.utils.translation import gettext as _


def signed_in_recently(request) -> bool:
    """Whether the newest sign-in on this session is within allauth's reauthentication window."""
    records = get_authentication_records(request)
    return bool(records) and time.time() - records[-1]["at"] < app_settings.REAUTHENTICATION_TIMEOUT


def reauthentication_required(function=None, **options):
    """allauth's decorator, plus a recent sign-in for an account with nothing to type again."""

    def decorator(view):
        asked_by_allauth = allauth_requires(view, **options)

        @wraps(view)
        def wrapper(request, *args, **kwargs):
            pass_method = options.get("allow_get") and request.method == "GET"
            user = request.user
            if (
                user.is_authenticated
                and not pass_method
                and not get_reauthentication_flows(user)
                and not signed_in_recently(request)
            ):
                path = request.get_full_path() if request.method == "GET" else ""
                logout(request)
                messages.info(
                    request,
                    _("For this, sign in again with your identity provider, then try it."),
                )
                query = urlencode({REDIRECT_FIELD_NAME: path}) if path else ""
                return redirect(reverse("account_login") + (f"?{query}" if query else ""))
            return asked_by_allauth(request, *args, **kwargs)

        return wrapper

    return decorator(function) if function else decorator
