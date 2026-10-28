"""Settings → Export in a browser: the page, an error on it, and the dialog it changes (#878).

The walk in `test_every_page.py` reaches the page in its first state; this looks at the two
states it does not: a refusal drawn beside the value that caused it, and a CV page whose
export dialog says that every file is stripped instead of showing its fields.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from .test_accessibility import axe_source, describe, sign_in, violations_on  # noqa: F401

pytestmark = pytest.mark.e2e


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_page_and_a_refusal_on_it_have_no_violations(
    live_server,
    page: Page,
    axe_source,  # noqa: F811
    applicant,
    scheme,
):
    page.emulate_media(color_scheme=scheme)
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/settings/export/")
    found = violations_on(page, axe_source)
    assert not found, describe(f"Settings → Export ({scheme})", found)

    # *Always this* for the author, with nothing typed: refused, beside the field.
    page.locator('[data-export-property="author"]').get_by_label("Always this").check()
    page.get_by_role("button", name="Save").click()
    expect(page.locator('[data-export-property="author"] [data-invalid="true"]')).to_have_count(1)
    found = violations_on(page, axe_source)
    assert not found, describe(f"Settings → Export with a refusal ({scheme})", found)


def test_strip_all_is_saved_and_the_cv_page_says_so(
    live_server,
    page: Page,
    axe_source,  # noqa: F811
    applicant,
):
    from postulo.documents.models import CV

    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/settings/export/")
    page.get_by_label("Strip all metadata").check()
    page.get_by_role("button", name="Save").click()
    expect(page.get_by_label("Strip all metadata")).to_be_checked()

    cv = CV.objects.get(owner=applicant)
    page.goto(f"{live_server.url}{cv.get_absolute_url()}")
    expect(page.locator("[data-metadata-stripped]")).to_be_visible()
    found = violations_on(page, axe_source)
    assert not found, describe("a CV whose files are stripped", found)
