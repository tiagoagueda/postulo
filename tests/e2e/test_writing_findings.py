"""The list of what will go out wrong, in a real browser (#1258).

The markup, the rules and who may write are held by the unit suite (`test_placeholder_checks`,
`test_writing_apply`, `security/test_writing_apply`). What only a browser can say: that a
replacement is a press that works with scripts on and with scripts off and lands back on the
list with the section focused, and that the page passes axe with the list drawn, in both
themes.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Browser, Page, expect

from tests.e2e.signing_in import sign_in
from tests.e2e.test_accessibility import AXE, describe, violations_on

pytestmark = pytest.mark.e2e


@pytest.fixture
def letter(applicant):
    from postulo.applications.models import Application, Status
    from postulo.documents.models import CoverLetter
    from postulo.jobs.models import Company, JobPosting

    company = Company.objects.create(owner=applicant, name="Aperture Science")
    posting = JobPosting.objects.create(owner=applicant, company=company, title="Test engineer")
    Application.objects.create(owner=applicant, posting=posting, status=Status.APPLIED)
    return CoverLetter.objects.create(
        owner=applicant,
        name="To Aperture",
        body="Dear {{ compnay }},\n\nAbout the {{ role }} in {{ location }}.\n\n{{ name }}",
    )


def replace_and_undo(page: Page, base: str, letter) -> None:
    sign_in(page, base, f"/documents/letters/{letter.pk}/")
    section = page.locator("#writing-findings")
    expect(section.get_by_role("status")).to_have_text("1 thing will go out wrong.")
    expect(section.locator("[data-finding]")).to_have_count(1)

    section.get_by_role("button", name="Replace “{{ compnay }}” with “{{ company }}”").click()

    expect(page).to_have_url(f"{base}/documents/letters/{letter.pk}/#writing-findings")
    expect(page.get_by_role("status").filter(has_text="Replaced")).to_be_visible()
    expect(section.get_by_role("status")).to_have_text("Nothing to point out.")
    expect(section.locator("[data-finding]")).to_have_count(0)
    letter.refresh_from_db()
    assert letter.body.startswith("Dear {{ company }},")

    section.get_by_role("button", name="Undo the last replacement").click()

    expect(page.get_by_role("status").filter(has_text="Put back as it was.")).to_be_visible()
    letter.refresh_from_db()
    assert letter.body.startswith("Dear {{ compnay }},")


def test_a_replacement_is_one_press_and_can_be_undone(page: Page, live_server, letter):
    replace_and_undo(page, live_server.url, letter)


def test_a_replacement_works_with_scripts_off(browser: Browser, live_server, letter):
    context = browser.new_context(java_script_enabled=False)
    page = context.new_page()
    try:
        replace_and_undo(page, live_server.url, letter)
    finally:
        context.close()


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_a_letters_page_with_its_list_passes_axe(page: Page, live_server, letter, scheme):
    from postulo.applications.models import Application

    application = Application.objects.get(owner=letter.owner)
    page.emulate_media(color_scheme=scheme)
    sign_in(page, live_server.url, f"/documents/letters/{letter.pk}/?application={application.pk}")
    expect(page.locator("#writing-findings mark")).to_have_count(2)

    found = violations_on(page, AXE.read_text(encoding="utf-8"))

    assert not found, describe(page.url, found)
