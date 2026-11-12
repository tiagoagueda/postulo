"""The skills panel of a listing in a real browser (#1093): both themes, right to left, a phone.

What the server draws is in `tests/test_listing_skills.py`. Here the panel is drawn with the
skills it names held and missing, and the CV that prints the most of them, under axe, at
320 pixels without a sideways scroll, and under ``dir="rtl"``. The skills are the few of
`tests/conftest.py::ESCO_SKILLS`, so the walk needs no download.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.signing_in import sign_in
from tests.e2e.test_accessibility import AXE, describe, violations_on

pytestmark = pytest.mark.e2e

TEXT = "You know **SQL** and Python, have led project management, and can manage budgets."


@pytest.fixture(scope="session")
def axe() -> str:
    if not AXE.is_file():
        pytest.skip("axe-core is not installed; run npm ci")
    return AXE.read_text(encoding="utf-8")


@pytest.fixture
def skilled_listing(esco_skills, applicant):
    from django.contrib.contenttypes.models import ContentType

    from postulo.documents.models import CV, CVItem
    from postulo.jobs.models import Company, JobPosting
    from postulo.resume.models import Skill, SkillGroup

    group = SkillGroup.objects.create(owner=applicant, name="Data")
    for name in ("SQL", "Project management"):
        Skill.objects.create(owner=applicant, group=group, name=name)
    cv = CV.objects.create(owner=applicant, name="Data roles")
    CVItem.objects.create(
        owner=applicant,
        cv=cv,
        content_type=ContentType.objects.get_for_model(SkillGroup),
        object_id=group.pk,
    )
    company = Company.objects.create(owner=applicant, name="Aperture Science")
    return JobPosting.objects.create(
        owner=applicant,
        company=company,
        title="Data Engineer",
        description=TEXT,
        description_format="markdown",
    )


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_panel_has_no_violations(live_server, page: Page, axe, skilled_listing, scheme):
    page.emulate_media(color_scheme=scheme)
    sign_in(page, live_server.url)
    page.goto(live_server.url + skilled_listing.get_absolute_url())
    panel = page.locator("[data-listing-skills]")
    expect(panel.locator("[data-skills-held] li")).to_have_count(2)
    expect(panel.locator("[data-skills-missing] li")).to_have_count(2)
    expect(panel.get_by_role("link", name="Data roles")).to_be_visible()
    found = violations_on(page, axe)
    assert not found, describe(f"listing with its skills ({scheme})", found)


def test_the_panel_fits_a_phone_and_reads_right_to_left(
    live_server, page: Page, axe, skilled_listing, applicant
):
    profile = applicant.profile
    profile.language = "ar"
    profile.save(update_fields=["language"])
    page.set_viewport_size({"width": 320, "height": 800})
    sign_in(page, live_server.url)
    page.goto(live_server.url + skilled_listing.get_absolute_url())
    expect(page.locator("html")).to_have_attribute("dir", "rtl")
    panel = page.locator("[data-listing-skills]")
    expect(panel.locator("[data-skills-held] li")).to_have_count(2)
    sideways = page.evaluate(
        "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )
    assert sideways <= 0, f"the page scrolls {sideways}px sideways at 320px"
    found = violations_on(page, axe)
    assert not found, describe("listing with its skills (rtl, 320px)", found)
