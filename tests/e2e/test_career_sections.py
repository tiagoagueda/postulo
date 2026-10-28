"""The career page's sidebar, in a browser: it navigates, and it says where you are (#175).

The markup is checked next to the templates; what only a browser can show is that an
anchor takes you to its section, and that the entry for the section on the screen is the
one marked -- `aria-current="location"`, set by app.js as the page is read.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from .test_accessibility import furnished, sign_in  # noqa: F401

pytestmark = pytest.mark.e2e


def test_an_entry_takes_you_to_its_section_and_is_marked_there(live_server, page: Page, furnished):  # noqa: F811
    page.set_viewport_size({"width": 1280, "height": 700})
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/career/")

    nav = page.locator('nav[aria-label="Career sections"]')
    expect(nav.locator("a")).to_have_count(15)

    nav.locator('a[href="#section-reference"]').click()

    section = page.locator("#section-reference")
    box = section.bounding_box()
    assert box and 0 <= box["y"] < 700, "the section was not brought onto the screen"
    expect(nav.locator('a[href="#section-reference"]')).to_have_attribute(
        "aria-current", "location"
    )
    expect(nav.locator('a[href="#section-experience"]')).not_to_have_attribute(
        "aria-current", "location"
    )


def test_on_a_phone_the_list_is_a_strip_above_the_sections(live_server, page: Page, furnished):  # noqa: F811
    page.set_viewport_size({"width": 390, "height": 844})
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/career/")

    nav = page.locator('nav[aria-label="Career sections"]').bounding_box()
    first = page.locator("#section-experience").bounding_box()
    assert nav and first
    assert nav["y"] + nav["height"] <= first["y"] + 1, "the list sits above the sections"
    assert nav["width"] <= 390, "the strip scrolls inside itself rather than widening the page"


def label_text(page: Page) -> str:
    return page.locator("[data-section-label]").inner_text().strip()


@pytest.mark.parametrize(
    ("address", "name"), [("/career/", "Your career"), ("/accounts/profile/", "Your details")]
)
def test_the_label_names_the_page_once_its_heading_is_behind_the_masthead(
    live_server,
    page: Page,
    furnished,  # noqa: F811
    applicant,
    address,
    name,
):
    """The label says *On this page* until the page's heading scrolls away, then the page's
    name, larger and as a title, whichever section is being read, and the text again on the
    way back (#869, which replaced the section's title of #677)."""
    import datetime as dt

    from postulo.resume.models import Experience

    for year in range(2000, 2012):
        Experience.objects.create(
            owner=applicant,
            organisation=f"Firm {year}",
            role="Engineer",
            start_date=dt.date(year, 1, 1),
            summary="Kept things running.",
        )
    page.set_viewport_size({"width": 1280, "height": 500})
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}{address}")
    label = page.locator("[data-section-label]")
    expect(label).to_have_text("On this page")
    assert page.locator("[data-section-label][aria-live]").count() == 0
    small = page.evaluate(
        "(el) => parseFloat(getComputedStyle(el).fontSize)", label.element_handle()
    )

    heading = page.locator("main h1").first
    bottom = page.evaluate(
        "(el) => el.getBoundingClientRect().bottom + window.scrollY", heading.element_handle()
    )
    header = page.locator("header").first.bounding_box()["height"]
    page.evaluate(f"window.scrollTo(0, {bottom - header + 4})")
    expect(label).to_have_text(name)
    large = page.evaluate(
        "(el) => parseFloat(getComputedStyle(el).fontSize)", label.element_handle()
    )
    assert large > small, "the page's name is drawn larger than the hint was"

    # Deep in a section it is still the page's name, not the section's.
    page.evaluate(
        """() => document.getElementById(
            document.querySelectorAll("[data-section-link]")[2].dataset.sectionLink
        ).scrollIntoView()"""
    )
    page.evaluate("window.scrollBy(0, 100)")
    expect(label).to_have_text(name)

    page.evaluate("window.scrollTo(0, 0)")
    expect(label).to_have_text("On this page")
    assert (
        page.evaluate("(el) => parseFloat(getComputedStyle(el).fontSize)", label.element_handle())
        == small
    )


def test_on_a_phone_the_label_stays_as_it_is(live_server, page: Page, furnished):  # noqa: F811
    page.set_viewport_size({"width": 390, "height": 844})
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/career/")

    page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
    # A fixed wait on purpose: the label must stay unchanged past the scroll event and the
    # animation frame app.js marks it in.
    page.wait_for_timeout(200)
    assert label_text(page) == "On this page"


def test_a_search_hit_lands_on_its_section_below_the_masthead(live_server, page: Page, furnished):  # noqa: F811
    """The address a career hit carries (#707) names an id the page has, and the browser
    scrolls to it clear of the sticky header, as it does for the sidebar's anchors."""
    page.set_viewport_size({"width": 1280, "height": 700})
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/search/?q=engineer")

    hit = page.locator('a[href*="/career/#"]').first
    fragment = hit.get_attribute("href").split("#")[1]
    hit.click()

    page.wait_for_url(f"**/career/#{fragment}")
    box = page.locator(f"#{fragment}").bounding_box()
    header = page.locator("header").first.bounding_box()
    assert box and 0 <= box["y"] < 700, "the section was not brought onto the screen"
    assert box["y"] >= header["height"] - 1, "the section is hidden behind the masthead"
