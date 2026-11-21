"""*Search a job service* in a browser (#1253).

The service is the fake one `tests/test_listing_search.py` declares, behind the real guarded
client: the live server runs in this process, so the transport stubbed there is the one its
requests meet, and nothing leaves the machine. With scripts off the whole of it is plain form
posts; axe reads the form and the results in both themes.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.signing_in import sign_in
from tests.e2e.test_accessibility import (  # noqa: F401 - fixtures, by name
    axe_source,
    describe,
    violations_on,
)
from tests.test_listing_search import service  # noqa: F401 - a fixture, by name

pytestmark = pytest.mark.e2e


@pytest.fixture
def switched_on(db):
    from django.core.cache import caches

    from postulo.core.memo import forget_decisions
    from postulo.jobs import listing_search
    from postulo.plugins.models import PluginPolicy

    PluginPolicy.objects.filter(plugin=listing_search.FEATURE, person=None).delete()
    forget_decisions()
    caches["ratelimit"].clear()


def test_search_and_review_with_scripts_off(
    browser,
    live_server,
    applicant,
    service,  # noqa: F811
    switched_on,
):
    _answers, seen = service
    context = browser.new_context(java_script_enabled=False)
    page = context.new_page()
    try:
        sign_in(page, live_server.url, "/listings/")
        page.get_by_role("link", name="Search a job service").click()
        expect(page.get_by_role("heading", name="Search a job service")).to_be_visible()
        assert seen == [], "nothing is asked on opening the page"

        page.get_by_label("Words").fill("nurse")
        page.get_by_role("button", name="Search", exact=True).click()
        expect(page.get_by_text("41 adverts found")).to_be_visible()
        page.locator('[data-search-hit="1001"]').get_by_role("button").click()

        expect(page.get_by_role("heading", name="Review the capture")).to_be_visible()
        expect(page.get_by_label("Title")).to_have_value("Nurse, night shifts")
    finally:
        context.close()


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_form_and_the_results_have_no_violations(
    page: Page,
    live_server,
    applicant,
    service,  # noqa: F811
    switched_on,
    axe_source,  # noqa: F811
    scheme,
):
    page.emulate_media(color_scheme=scheme)
    sign_in(page, live_server.url, "/listings/search/")

    found = violations_on(page, axe_source)
    assert not found, describe(f"/listings/search/ ({scheme})", found)

    page.get_by_label("Words").fill("nurse")
    page.get_by_role("button", name="Search", exact=True).click()
    expect(page.get_by_text("41 adverts found")).to_be_visible()
    found = violations_on(page, axe_source)
    assert not found, describe(f"/listings/search/ with results ({scheme})", found)
