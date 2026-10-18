"""Signing in for a browser test without filling the sign-in form (#720).

Every browser test used to sign in through the form: open the page, type the address and
the password, submit, wait for the dashboard, and then usually go somewhere else -- two page
loads in a fresh context with nothing cached, in every one of hundreds of tests. Only the
tests about signing in need the form (`test_smoke.py`, `test_passkeys.py`, `test_csp.py`);
the rest need a signed-in browser, which a session made on the server and handed to the
browser as its cookie is.
"""

from __future__ import annotations

import time

from playwright.sync_api import Page, expect

from tests.e2e.conftest import EMAIL


def sign_in(page: Page, base: str, path: str = "/", *, email: str = EMAIL) -> None:
    """Sign ``email`` in on ``page``'s context and open ``path``.

    The session is Django's own, made by `force_login` through allauth's backend, so
    `last_login` moves as a real sign-in moves it. allauth also keeps a record of how the
    person signed in, which the form writes and `force_login` does not; without it every
    page behind a recent-password check -- deleting the account, two-factor set-up, changing
    an address -- would send the test to the reauthentication page. The record is good for
    allauth's five minutes, as a typed password is.
    """
    from allauth.account.internal.flows.login import AUTHENTICATION_METHODS_SESSION_KEY
    from django.conf import settings
    from django.contrib.auth import get_user_model
    from django.test import Client

    user = get_user_model().objects.get(email=email)
    client = Client()
    client.force_login(user, backend="allauth.account.auth_backends.AuthenticationBackend")
    session = client.session
    session[AUTHENTICATION_METHODS_SESSION_KEY] = [
        {"method": "password", "at": time.time(), "email": email, "reauthenticated": True}
    ]
    session.save()
    page.context.add_cookies(
        [{"name": settings.SESSION_COOKIE_NAME, "value": session.session_key, "url": base}]
    )
    page.goto(f"{base}{path}")
    expect(page).to_have_url(f"{base}{path}")


def confirm_password(page: Page, password: str) -> bool:
    """Answer allauth's "Confirm" page, if the browser is on it; whether it was.

    Since allauth 65.20 a sign-in no longer stands in for typing the password again (#987), so
    a sensitive step reached after a form or passkey sign-in, or after the five minutes of
    the cookie sign-in, stops here. The button pressed is the one of the form that holds the
    password field: the page also carries the navigation's *Sign out* form, and the first
    button of "a form" was that one.
    """
    if "reauthenticate" not in page.url:
        return False
    page.locator("input[name=password]").fill(password)
    page.locator("form:has(input[name=password])").get_by_role("button").first.click()
    page.wait_for_url(lambda url: "reauthenticate" not in url)
    return True
