"""The skill box offers the ESCO classification's names as it is typed (#266).

Fourteen thousand names are too many for a `<datalist>` sent with the page, so htmx asks
for the ones beginning with what has been typed and puts them in the list. What a browser
has to show, and a unit test cannot: that the request goes, under the content security
policy, and that the options arrive where the box reads them. With scripts off the box is
the plain text box it always was, and the name is matched when it is saved.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from .conftest import EMAIL, PASSWORD

pytestmark = pytest.mark.e2e


def sign_in(page: Page, base: str) -> None:
    page.goto(f"{base}/accounts/login/")
    page.locator("input[name=login]").fill(EMAIL)
    page.locator("input[name=password]").fill(PASSWORD)
    page.locator("form").get_by_role("button", name="Sign In", exact=True).click()
    expect(page).to_have_url(f"{base}/")


def test_the_box_is_offered_names_as_it_is_typed(live_server, page: Page, applicant, esco_skills):
    from postulo.resume.models import Skill, SkillGroup

    SkillGroup.objects.create(owner=applicant, name="Management")
    base = live_server.url
    sign_in(page, base)
    page.goto(f"{base}/career/skill/new/")

    box = page.locator("input[name=name]")
    box.press_sequentially("proj", delay=60)
    options = page.locator("#skill-suggestions option")
    expect(options).to_have_count(1)
    expect(options.first).to_have_attribute("value", "project management")

    box.fill("project management")
    page.locator("select[name=group]").select_option(label="Management")
    page.get_by_role("button", name="Save", exact=True).click()
    page.wait_for_url(f"{base}/career/")
    assert (
        Skill.objects.for_user(applicant)
        .get()
        .esco_uri.endswith("7111b95d-0ce3-441a-9d92-4c75d05c4388")
    )


def test_with_scripts_off_it_is_a_text_box_and_the_name_is_matched_on_save(
    live_server, browser, applicant, esco_skills
):
    from postulo.resume.models import Skill, SkillGroup

    SkillGroup.objects.create(owner=applicant, name="Management")
    context = browser.new_context(java_script_enabled=False)
    page = context.new_page()
    try:
        base = live_server.url
        sign_in(page, base)
        page.goto(f"{base}/career/skill/new/")
        expect(page.locator("#skill-suggestions option")).to_have_count(0)

        page.locator("input[name=name]").fill("Project management")
        page.locator("select[name=group]").select_option(label="Management")
        page.get_by_role("button", name="Save", exact=True).click()
        page.wait_for_url(f"{base}/career/")
    finally:
        context.close()

    skill = Skill.objects.for_user(applicant).get()
    assert skill.name == "Project management"
    assert skill.esco_uri.endswith("7111b95d-0ce3-441a-9d92-4c75d05c4388")
