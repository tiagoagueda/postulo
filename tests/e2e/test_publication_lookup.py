"""*Look up* and *Suggest from my ORCID record* in a browser (#688).

The services are the same recorded answers `tests/test_publication_lookup.py` uses, behind
the real guarded client: the live server runs in this process, so the transport stubbed
here is the one its requests meet, and nothing leaves the machine.
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
from tests.test_publication_lookup import (  # noqa: F401 - fixtures, by name
    NUMPY_DOI,
    orcid_answers,
    registry,
    via_crossref,
    with_orcid,
)

pytestmark = pytest.mark.e2e


def test_look_up_fills_the_empty_boxes_with_scripts_off(
    browser,
    live_server,
    applicant,
    registry,  # noqa: F811
):
    answers, _seen = registry
    via_crossref(answers)
    context = browser.new_context(java_script_enabled=False)
    page = context.new_page()
    try:
        sign_in(page, live_server.url, "/career/publication/new/")
        page.get_by_label("DOI").fill(NUMPY_DOI)
        page.get_by_label("Title").fill("My own title")
        page.get_by_role("button", name="Look up").click()

        expect(page.get_by_text("Filled in from Crossref, through doi.org")).to_be_visible()
        expect(page.get_by_label("Title")).to_have_value("My own title")
        expect(page.get_by_label("Authors")).to_have_value(
            "Harris, Charles R.\nMillman, K. Jarrod\nvan der Walt, Stéfan J."
        )
        from postulo.resume.models import Publication

        assert not Publication.objects.exists(), "nothing kept before Save"
        page.get_by_role("button", name="Save", exact=True).last.click()
        expect(page.get_by_text("Added.")).to_be_visible()
    finally:
        context.close()

    held = Publication.objects.get()
    assert held.title == "My own title" and held.container_title == "Nature"


def test_enter_in_a_box_saves_and_does_not_look_up(
    page: Page,
    live_server,
    applicant,
    registry,  # noqa: F811
):
    _answers, seen = registry
    sign_in(page, live_server.url, "/career/publication/new/")
    page.get_by_label("DOI").fill(NUMPY_DOI)
    page.get_by_label("Title").fill("Saved with Enter")
    page.get_by_label("Title").press("Enter")

    expect(page.get_by_text("Added.")).to_be_visible()
    assert seen == [], "Enter saved; nothing was asked of doi.org"


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_form_and_the_orcid_review_have_no_violations(
    page: Page,
    live_server,
    applicant,
    registry,  # noqa: F811
    axe_source,  # noqa: F811
    scheme,
):
    answers, _seen = registry
    orcid_answers(answers)
    with_orcid(applicant)
    page.emulate_media(color_scheme=scheme)
    sign_in(page, live_server.url, "/career/publication/new/")

    found = violations_on(page, axe_source)
    assert not found, describe(f"/career/publication/new/ ({scheme})", found)

    page.goto(f"{live_server.url}/career/")
    page.get_by_role("button", name="Suggest from my ORCID record").click()
    expect(page.get_by_role("heading", name="From your ORCID record")).to_be_visible()
    found = violations_on(page, axe_source)
    assert not found, describe(f"/career/publications/orcid/ ({scheme})", found)

    page.get_by_role("checkbox").first.check()
    page.get_by_role("button", name="Add the ticked works").click()
    expect(page.get_by_text("Added 1 publication from your ORCID record")).to_be_visible()
