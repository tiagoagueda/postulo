"""A box that suggests what is about to be typed, in a real browser (#1251).

`tests/test_suggest.py` reads what the server answers and keeps one person's data from
another. What takes a browser is the combobox itself:

* **the pattern**: a combobox naming its listbox, `aria-expanded` and `aria-activedescendant`
  following the keys, the count said in a live region;
* **the keyboard**: Down and Up, Enter taking the option the keys are on and otherwise
  submitting as ever, Escape closing, Tab leaving without taking anything;
* **the pointer** takes an option without the box losing the focus first;
* **what is typed is never imposed**: another value is kept and saved;
* **the request volume**: one question for a burst of typing, none on load;
* **with scripts off** the box is a plain text box, and the browser's own list where it has one;
* **every covered field** on its own form;
* **a narrow window and right to left**, and **axe** in both themes with a list open.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from .test_accessibility import axe_source, describe, sign_in, violations_on  # noqa: F401
from .test_reflow import SCROLLS_SIDEWAYS

pytestmark = pytest.mark.e2e

NEW_APPLICATION = "/applications/new/"


@pytest.fixture
def recorded(applicant):
    """Three companies, two places and two sources the person has typed before."""
    from postulo.jobs.models import Company, JobPosting

    for company, title, place, source in [
        ("Aperture Science", "Test Engineer", "Cambridge", "LinkedIn"),
        ("Aperture Science", "Test Engineer", "Cambridge", "LinkedIn"),
        ("Black Mesa", "Research Associate", "Lyon", "A friend"),
        ("Apex Labs", "Platform Engineer", "Cambridgeshire", "LinkedIn"),
    ]:
        firm, _made = Company.objects.get_or_create(owner=applicant, name=company)
        JobPosting.objects.create(
            owner=applicant, company=firm, title=title, location=place, source=source
        )
    return applicant


def box(page: Page, name: str):
    return page.locator(f"input[name={name}]")


def type_into(page: Page, name: str, text: str):
    field = box(page, name)
    field.click()
    field.press_sequentially(text, delay=40)
    return field


def options(page: Page):
    return page.locator('[role="listbox"]:not([hidden]) [role="option"]')


# ------------------------------------------------------------------------------ pattern


def test_the_box_is_a_combobox_and_the_list_is_what_it_controls(live_server, page: Page, recorded):
    sign_in(page, live_server.url, NEW_APPLICATION)

    field = type_into(page, "company_name", "ap")

    expect(options(page)).to_have_count(2)
    expect(field).to_have_attribute("role", "combobox")
    expect(field).to_have_attribute("aria-expanded", "true")
    expect(field).to_have_attribute("aria-autocomplete", "list")
    listbox_id = field.get_attribute("aria-controls")
    expect(page.locator(f"#{listbox_id}")).to_have_attribute("role", "listbox")
    expect(page.locator(f"#{listbox_id}")).to_have_attribute("aria-label", "Company")
    # Both are in the list; the commonest, which was used twice, comes first.
    assert options(page).all_inner_texts() == ["Aperture Science", "Apex Labs"]
    expect(page.locator("[role=status].sr-only").first).to_contain_text("2 suggestions available")
    # The browser's own list is off the box while this one is drawn, so there is one list.
    expect(field).not_to_have_attribute("list", "company-suggestions")


def test_no_question_is_asked_before_somebody_types_and_a_burst_is_one_question(
    live_server, page: Page, recorded
):
    asked = []
    page.on(
        "request", lambda request: asked.append(request.url) if "/suggest/" in request.url else None
    )
    sign_in(page, live_server.url, NEW_APPLICATION)
    page.wait_for_timeout(500)
    assert asked == []

    type_into(page, "company_name", "aper")
    expect(options(page)).to_have_count(1)
    page.wait_for_timeout(400)

    assert len(asked) == 1, asked


def test_the_same_word_is_not_asked_twice(live_server, page: Page, recorded):
    asked = []
    page.on(
        "request", lambda request: asked.append(request.url) if "/suggest/" in request.url else None
    )
    sign_in(page, live_server.url, NEW_APPLICATION)

    field = type_into(page, "company_name", "bl")
    expect(options(page)).to_have_count(1)
    field.press("Backspace")
    field.press("Backspace")
    field.press_sequentially("bl", delay=40)
    expect(options(page)).to_have_count(1)
    page.wait_for_timeout(400)

    assert len(asked) == 1, asked  # typed twice, asked once


# ------------------------------------------------------------------------------ keyboard


def test_the_keys_move_through_the_list_and_enter_takes_one(live_server, page: Page, recorded):
    sign_in(page, live_server.url, NEW_APPLICATION)
    field = type_into(page, "company_name", "ap")
    expect(options(page)).to_have_count(2)

    field.press("ArrowDown")
    first = options(page).nth(0)
    expect(first).to_have_attribute("aria-selected", "true")
    expect(field).to_have_attribute("aria-activedescendant", first.get_attribute("id"))
    field.press("ArrowDown")
    expect(options(page).nth(1)).to_have_attribute("aria-selected", "true")
    expect(first).to_have_attribute("aria-selected", "false")
    field.press("ArrowDown")  # wraps
    expect(first).to_have_attribute("aria-selected", "true")
    field.press("ArrowUp")  # wraps back
    expect(options(page).nth(1)).to_have_attribute("aria-selected", "true")

    field.press("Enter")

    expect(field).to_have_value("Apex Labs")
    expect(field).to_have_attribute("aria-expanded", "false")
    expect(field).to_be_focused()
    assert page.url.endswith(NEW_APPLICATION), "Enter on a suggestion does not submit the form"


def test_escape_closes_and_tab_takes_nothing(live_server, page: Page, recorded):
    sign_in(page, live_server.url, NEW_APPLICATION)
    field = type_into(page, "company_name", "ap")
    expect(options(page)).to_have_count(2)

    field.press("ArrowDown")
    field.press("Escape")
    expect(field).to_have_attribute("aria-expanded", "false")
    expect(field).to_have_value("ap")

    field.press("ArrowDown")  # asks again and opens
    expect(options(page)).to_have_count(2)
    field.press("ArrowDown")
    field.press("Tab")
    expect(field).to_have_value("ap")
    expect(field).not_to_be_focused()


def test_down_on_an_empty_box_offers_the_commonest(live_server, page: Page, recorded):
    sign_in(page, live_server.url, NEW_APPLICATION)
    field = box(page, "source")
    field.focus()

    field.press("ArrowDown")

    expect(options(page)).to_have_count(2)
    assert options(page).all_inner_texts()[0] == "LinkedIn"


def test_a_click_takes_an_option(live_server, page: Page, recorded):
    sign_in(page, live_server.url, NEW_APPLICATION)
    type_into(page, "location", "lyo")

    options(page).first.click()

    expect(box(page, "location")).to_have_value("Lyon")
    expect(box(page, "location")).to_be_focused()


def test_another_value_is_kept_and_saved(live_server, page: Page, recorded):
    from postulo.applications.models import Application

    base = live_server.url
    sign_in(page, base, NEW_APPLICATION)
    type_into(page, "company_name", "ap")
    expect(options(page)).to_have_count(2)
    box(page, "company_name").fill("Aperture Laboratories")
    box(page, "title").fill("Welder")
    expect(options(page)).to_have_count(0)  # the title box has none for that
    page.get_by_role("button", name="Save", exact=True).first.click()
    page.wait_for_url(lambda url: "/new/" not in url, timeout=10_000)

    assert Application.objects.filter(posting__company__name="Aperture Laboratories").exists()


def test_a_dead_answer_leaves_a_plain_box(live_server, page: Page, recorded):
    page.route("**/suggest/**", lambda route: route.fulfill(status=429, body="slow down"))
    sign_in(page, live_server.url, NEW_APPLICATION)

    field = type_into(page, "company_name", "ap")
    page.wait_for_timeout(600)

    expect(options(page)).to_have_count(0)
    expect(field).to_have_attribute("aria-expanded", "false")
    field.fill("Anything")
    expect(field).to_have_value("Anything")


# ------------------------------------------------------------------------------ no script


def test_with_scripts_off_it_is_a_text_box_with_the_browsers_own_list(
    live_server, browser, recorded
):
    context = browser.new_context(java_script_enabled=False)
    page = context.new_page()
    try:
        sign_in(page, live_server.url, NEW_APPLICATION)
        field = box(page, "company_name")
        expect(field).not_to_have_attribute("role", "combobox")
        expect(field).to_have_attribute("list", "company-suggestions")
        expect(page.locator("#company-suggestions option")).to_have_count(3)
        expect(page.locator("[role=listbox]")).to_have_count(0)
        field.fill("Whatever Ltd")
        box(page, "title").fill("Welder")
        page.get_by_role("button", name="Save", exact=True).first.click()
        page.wait_for_url(lambda url: "/new/" not in url, timeout=10_000)
    finally:
        context.close()


# ------------------------------------------------------------------------- every field


@pytest.mark.parametrize(
    ("path", "name", "typed", "wanted"),
    [
        (NEW_APPLICATION, "company_name", "black", "Black Mesa"),
        (NEW_APPLICATION, "title", "platf", "Platform Engineer"),
        (NEW_APPLICATION, "location", "lyo", "Lyon"),
        (NEW_APPLICATION, "source", "frie", "A friend"),
        ("/jobs/contacts/new/", "role", "recr", "Recruiter"),
        ("/career/experience/new/", "organisation", "apex", "Apex Labs"),
    ],
)
def test_each_covered_field_suggests_its_own_data(
    live_server, page: Page, recorded, path, name, typed, wanted
):
    from postulo.jobs.models import Company, Contact

    Contact.objects.create(
        owner=recorded,
        company=Company.objects.filter(owner=recorded).first(),
        name="Ann",
        role="Recruiter",
    )
    sign_in(page, live_server.url, path)

    field = type_into(page, name, typed)

    expect(options(page).first).to_have_text(wanted)
    field.press("ArrowDown")
    field.press("Enter")
    expect(field).to_have_value(wanted)


# -------------------------------------------------------------------- narrow, rtl, axe


def test_nothing_scrolls_sideways_at_320_with_a_list_open(live_server, page: Page, recorded):
    from postulo.jobs.models import Company, JobPosting

    firm = Company.objects.filter(owner=recorded).first()
    JobPosting.objects.create(
        owner=recorded,
        company=firm,
        title="Chief Executive Officer of Extraordinarily-Long-Hyphenated-Company-Names",
        location="Llanfairpwllgwyngyllgogerychwyrndrobwllllantysiliogogogoch",
    )
    page.set_viewport_size({"width": 320, "height": 640})
    sign_in(page, live_server.url, NEW_APPLICATION)

    type_into(page, "location", "llan")
    expect(options(page)).to_have_count(1)

    assert page.evaluate(SCROLLS_SIDEWAYS)["reached"] == 0
    listbox = page.locator("[role=listbox]:visible").first.bounding_box()
    assert listbox["x"] >= 0 and listbox["x"] + listbox["width"] <= 320


def test_the_list_sits_under_the_box_right_to_left(live_server, page: Page, recorded):
    profile = recorded.profile
    profile.language = "ar"
    profile.save(update_fields=["language"])
    sign_in(page, live_server.url, NEW_APPLICATION)

    field = type_into(page, "location", "lyo")
    expect(options(page)).to_have_count(1)

    assert page.evaluate("document.documentElement.dir") == "rtl"
    under = page.locator("[role=listbox]:visible").first.bounding_box()
    above = field.bounding_box()
    assert abs(under["x"] - above["x"]) <= 1 and abs(under["width"] - above["width"]) <= 1
    assert under["y"] >= above["y"] + above["height"] - 1


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("language", ["en", "ar"])
def test_axe_is_clean_with_a_list_open_and_a_key_on_an_option(
    live_server,
    page: Page,
    recorded,
    axe_source,  # noqa: F811
    scheme,
    language,
):
    if language != "en":
        profile = recorded.profile
        profile.language = language
        profile.save(update_fields=["language"])
    page.emulate_media(color_scheme=scheme)
    sign_in(page, live_server.url, NEW_APPLICATION)

    field = type_into(page, "company_name", "ap")
    expect(options(page)).to_have_count(2)
    field.press("ArrowDown")

    found = violations_on(page, axe_source)
    assert not found, describe(f"{NEW_APPLICATION} ({scheme}, {language})", found)
