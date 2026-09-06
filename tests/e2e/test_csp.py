"""The content security policy, checked in a browser that is actually enforcing it.

Postulo promises pages with no inline script and nothing loaded from anybody else, and
production says so in a policy the browser enforces. A `SECURE_CSP` dictionary in a settings
file is not evidence that the pages obey it: the way to know is to serve them under the
policy and read what the browser complains about.

It complained on every page. htmx injects a `<style>` element as it starts, carrying rules
Postulo's own stylesheet already has, and `style-src 'self'` refuses it — so every visitor
was collecting a violation, and any genuine one would have been lost in them.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page

from .conftest import EMAIL, PASSWORD

pytestmark = pytest.mark.e2e


@pytest.fixture
def strict_csp(settings):
    """Exactly what `config/settings/prod.py` sends."""
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


def watch(page: Page) -> list[str]:
    """Every policy complaint the browser makes, as it makes it."""
    breaches: list[str] = []
    page.on(
        "console",
        lambda message: (
            breaches.append(f"{page.url}: {message.text}")
            if "Content Security Policy" in message.text
            else None
        ),
    )
    return breaches


def test_the_entrance_pages_keep_the_policy(live_server, page: Page, db, strict_csp):
    breaches = watch(page)
    for path in ("/", "/accounts/login/", "/accounts/password/reset/"):
        page.goto(f"{live_server.url}{path}")
        page.wait_for_load_state("networkidle")
    assert not breaches, "\n".join(breaches)


def test_the_pages_behind_a_sign_in_keep_it_too(live_server, page: Page, applicant, strict_csp):
    base = live_server.url
    breaches = watch(page)

    page.goto(f"{base}/accounts/login/")
    page.locator("input[name=login]").fill(EMAIL)
    page.locator("input[name=password]").fill(PASSWORD)
    page.locator("form").get_by_role("button", name="Sign In", exact=True).click()
    page.wait_for_url(f"{base}/")

    for path in (
        "/applications/",
        "/applications/board/",
        "/applications/insights/",
        "/documents/cvs/",
        "/settings/account/",
        "/settings/appearance/",
        "/search/?q=engineer",
    ):
        page.goto(f"{base}{path}")
        page.wait_for_load_state("networkidle")

    assert not breaches, "\n".join(breaches)


def test_a_table_narrowing_as_you_type_keeps_it(live_server, page: Page, applicant, strict_csp):
    """The htmx paths, since htmx is what was breaching it."""
    base = live_server.url
    breaches = watch(page)

    page.goto(f"{base}/accounts/login/")
    page.locator("input[name=login]").fill(EMAIL)
    page.locator("input[name=password]").fill(PASSWORD)
    page.locator("form").get_by_role("button", name="Sign In", exact=True).click()
    page.wait_for_url(f"{base}/")

    page.goto(f"{base}/applications/")
    field = page.locator("input[type=search]").first
    if field.count():
        field.fill("engineer")
        page.wait_for_timeout(600)

    assert not breaches, "\n".join(breaches)
