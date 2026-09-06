"""Passkeys, driven end to end with a virtual authenticator.

Chromium can pretend to be a security key over the DevTools protocol, which is the only way
to exercise this without a fingerprint reader in the test runner. What it exercises is the
whole path: the browser's own WebAuthn API, allauth's scripts, the forms, and the sign-in
that follows.

The thing most worth checking here is not that WebAuthn works — that is allauth's and the
browser's — but that it works **under Postulo's content security policy**, which allows no
inline script. allauth's passkey pages carry a `<script type="application/json">` data block
that its onload script reads. A data block is not a script and the policy does not apply to
it, but that is a sentence in a specification; the way to know is to run it with the policy
on and see whether the button does anything.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from .conftest import EMAIL, PASSWORD

pytestmark = pytest.mark.e2e


@pytest.fixture
def strict_csp(settings):
    """The production policy, on the pages this test visits."""
    from django.utils.csp import CSP

    settings.SECURE_CSP = {
        "default-src": [CSP.NONE],
        "script-src": [CSP.SELF],
        "style-src": [CSP.SELF],
        "img-src": [CSP.SELF, "data:"],
        "font-src": [CSP.SELF],
        "connect-src": [CSP.SELF],
        "form-action": [CSP.SELF],
        "frame-ancestors": [CSP.NONE],
        "base-uri": [CSP.SELF],
    }
    return settings


@pytest.fixture
def authenticator(page: Page):
    """A virtual passkey that lives for one test."""
    client = page.context.new_cdp_session(page)
    client.send("WebAuthn.enable", {"enableUI": False})
    result = client.send(
        "WebAuthn.addVirtualAuthenticator",
        {
            "options": {
                "protocol": "ctap2",
                "transport": "internal",
                "hasResidentKey": True,
                "hasUserVerification": True,
                "isUserVerified": True,
                "automaticPresenceSimulation": True,
            }
        },
    )
    yield result["authenticatorId"]


def sign_in(page: Page, base: str) -> None:
    page.goto(f"{base}/accounts/login/")
    page.locator("input[name=login]").fill(EMAIL)
    page.locator("input[name=password]").fill(PASSWORD)
    page.locator("form").get_by_role("button", name="Sign In", exact=True).click()
    page.wait_for_url(f"{base}/")


def test_a_passkey_can_be_added_and_then_signs_somebody_in(
    live_server, page: Page, applicant, authenticator, strict_csp
):
    base = live_server.url
    violations: list[str] = []
    page.on(
        "console",
        lambda message: (
            violations.append(message.text) if "Content Security Policy" in message.text else None
        ),
    )

    sign_in(page, base)

    # --- the account page offers it
    page.goto(f"{base}/settings/account/")
    expect(page.locator("[data-passkeys]")).to_have_attribute("data-passkeys", "0")
    page.get_by_role("link", name="Add one").click()
    if "reauthenticate" in page.url:
        page.locator("input[name=password]").fill(PASSWORD)
        page.locator("form").get_by_role("button").first.click()

    # --- registering one
    page.locator("input[name=name]").fill("The laptop")
    # The switch that makes it a way in rather than a second step. It starts on; ticking it
    # explicitly here is what makes this test about the flow rather than about the default.
    passwordless = page.locator("input[name=passwordless]")
    if passwordless.count() and not passwordless.is_checked():
        passwordless.check()
    page.get_by_role("button", name="Add").click()
    page.wait_for_load_state("networkidle")

    from allauth.mfa.models import Authenticator

    assert Authenticator.objects.filter(
        user=applicant, type=Authenticator.Type.WEBAUTHN
    ).exists(), f"no passkey was stored; console said: {violations}"

    # --- and it signs somebody in, with no password at all
    page.goto(f"{base}/accounts/logout/")
    page.get_by_role("button", name="Sign Out").click()
    page.wait_for_load_state("networkidle")

    page.goto(f"{base}/accounts/login/")
    page.get_by_role("button", name="Sign in with a passkey").click()
    expect(page).to_have_url(f"{base}/")

    assert not violations, f"the policy blocked something: {violations}"


def test_the_account_page_says_a_passkey_is_tied_to_this_address(
    live_server, page: Page, applicant
):
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/settings/account/")
    expect(page.locator("[data-passkeys-host]")).to_contain_text("localhost")
