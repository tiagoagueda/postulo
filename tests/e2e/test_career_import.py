"""Import a CV, in a browser: the page names no format and works with scripts off (#1260).

The unit tests post the form; what they cannot reach is the page as a person meets it. So
the one path is walked with scripts off -- choose a file, read it, see what is in it, add
it -- and the first step, which now lists what each importer reads, is measured by axe in
both themes. The review half has its own axe test in `test_accessibility.py`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from .test_accessibility import (  # noqa: F401
    axe_source,
    describe,
    sign_in,
    violations_on,
)

pytestmark = pytest.mark.e2e

EUROPASS = Path(__file__).resolve().parents[1] / "data" / "europass.xml"


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_page_that_lists_the_importers_has_no_violations(
    live_server,
    page: Page,
    axe_source,  # noqa: F811
    applicant,
    scheme,
):
    page.emulate_media(color_scheme=scheme)
    sign_in(page, live_server.url, "/career/import/")

    expect(page.get_by_role("heading", name="What can be read here")).to_be_visible()
    expect(page.get_by_text("Europass import", exact=True)).to_be_visible()
    found = violations_on(page, axe_source)
    assert not found, describe(f"/career/import/ ({scheme})", found)


def test_a_cv_is_read_and_added_with_scripts_off(browser, live_server, applicant):
    from postulo.resume.models import Experience

    context = browser.new_context(java_script_enabled=False)
    page = context.new_page()
    try:
        sign_in(page, live_server.url, "/career/import/")
        page.locator("input[type=file]").set_input_files(str(EUROPASS))
        page.get_by_role("button", name="Read it").click()

        expect(page.get_by_role("heading", name="What is in the file")).to_be_visible()
        expect(page.get_by_text("Read as Europass XML in the older format")).to_be_visible()
        assert not Experience.objects.filter(owner=applicant).exists()

        page.get_by_role("button", name="Add all of this").click()
        expect(page).to_have_url(f"{live_server.url}/career/")
    finally:
        context.close()

    assert Experience.objects.filter(owner=applicant).count() == 2
