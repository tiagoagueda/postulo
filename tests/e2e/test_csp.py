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
    """The policy production sends, because it is the one every settings module carries.

    `SECURE_CSP` is defined once in `config/settings/base.py` and inherited by the test and
    production modules alike, so this does not restate it: it used to, as a hand copy that
    went stale as the real policy gained `base-uri 'none'`, `frame-src`, `worker-src`, a
    nonce for the preview's inline style and `report-uri` (#984). All it does now is fail
    if something has replaced the policy with another, so these tests cannot pass under a
    policy nobody serves.
    """
    from postulo.config.settings import base

    assert settings.SECURE_CSP == base.SECURE_CSP, "the browser tests run under another policy"
    assert settings.SECURE_CSP, "no content security policy is being enforced at all"
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
        "/applications/?view=board",
        "/applications/?view=board&status=applied",
        "/applications/insights/",
        "/applications/report/",
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
        # A fixed wait on purpose: the window must outlast the 300 ms htmx input delay and
        # its swap so any violation they cause is heard, and the first search box need not
        # fire a request at all.
        page.wait_for_timeout(600)

    assert not breaches, "\n".join(breaches)


def test_a_table_with_a_stored_column_width_keeps_it(
    live_server, page: Page, applicant, strict_csp
):
    """A width is stored data applied to an element, which is the shape that breaches this.

    `style-src 'self'` refuses a `style` attribute as firmly as it refuses a `<style>`
    element, so a width written into the markup would be dropped by the browser and
    reported as a violation on every load -- silently doing nothing, which is the worst of
    both.
    """
    from postulo.accounts.models import Profile
    from postulo.jobs.models import Company

    Company.objects.create(owner=applicant, name="Aperture Science")
    Profile.objects.filter(user=applicant).update(
        table_settings={"companies": {"widths": {"name": 320}}}
    )
    base = live_server.url
    breaches = watch(page)

    page.goto(f"{base}/accounts/login/")
    page.locator("input[name=login]").fill(EMAIL)
    page.locator("input[name=password]").fill(PASSWORD)
    page.locator("form").get_by_role("button", name="Sign In", exact=True).click()
    page.wait_for_url(f"{base}/")

    page.goto(f"{base}/jobs/companies/")
    page.wait_for_load_state("networkidle")

    assert not breaches, "\n".join(breaches)
    assert page.locator('th[data-col="name"]').bounding_box()["width"] > 300, "and it applied"
