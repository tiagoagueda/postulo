"""The theme picker suggests the themes made for a document, and hides none (#1255).

The groups are a native `<optgroup>`, which the page script draws as Basecoat's group
headings; a page whose installed themes say nothing about what they suit draws no group.
"""

from __future__ import annotations

import pytest
from django.urls import reverse
from playwright.sync_api import Browser, Page, expect

from postulo.documents import themes

from .selects import open_list, options_of
from .signing_in import sign_in
from .test_accessibility import axe_source, describe, violations_on  # noqa: F401

pytestmark = pytest.mark.e2e


def a_report_theme(name: str, language: str) -> themes.Theme:
    return themes.Theme(
        name=name,
        label=name.title(),
        templates={themes.Kind.REPORT: f"documents/themes/plain/{themes.Kind.REPORT}.html"},
        languages=(language,),
    )


@pytest.fixture
def suited(applicant):
    """One theme made for the report's language, one for a language nobody writes in."""
    from postulo.applications import reports

    themes.register(a_report_theme("ours", reports.language_for(applicant)))
    themes.register(a_report_theme("theirs", "tlh"))
    yield
    themes.forget()


def test_the_suggested_themes_come_first_under_their_heading(
    page: Page,
    live_server,
    applicant,
    suited,
    axe_source,  # noqa: F811
):
    sign_in(page, live_server.url, reverse("applications:report"))
    select = page.locator("select[name=theme]")
    assert select.locator("optgroup").evaluate_all("(groups) => groups.map((g) => g.label)") == [
        "Suggested for this document",
        "Other themes",
    ]

    panel = open_list(page, select)
    expect(panel.locator("[data-select-heading]")).to_have_text(
        ["Suggested for this document", "Other themes"]
    )
    offered = options_of(page, select)
    expect(offered.first).to_have_text("Ours")
    expect(offered.filter(has_text="Theirs")).to_have_count(1)
    page.keyboard.press("Escape")

    found = violations_on(page, axe_source)
    assert not found, describe(page.url, found)


def test_with_scripts_off_the_groups_are_the_browsers_own(
    browser: Browser, live_server, applicant, suited
):
    context = browser.new_context(java_script_enabled=False)
    page = context.new_page()
    try:
        sign_in(page, live_server.url, reverse("applications:report"))
        select = page.locator("select[name=theme]")
        expect(select).to_be_visible()
        assert select.locator("optgroup").count() == 2
        assert select.locator("option").first.get_attribute("value") == "ours"
    finally:
        context.close()


def test_with_nothing_said_about_where_a_theme_suits_there_are_no_groups(
    page: Page, live_server, applicant
):
    sign_in(page, live_server.url, reverse("applications:report"))
    select = page.locator("select[name=theme]")
    expect(select).to_be_attached()
    assert select.locator("optgroup").count() == 0
