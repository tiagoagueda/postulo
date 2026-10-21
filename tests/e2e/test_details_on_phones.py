# ruff: noqa: F811, E501
"""*Your details* on a phone, and the cards it is made of (#919, #920, #922, #923, #924, #925,
#929, #930, #931).

What the server draws is in `tests/test_your_details_rows.py`; what needs a browser is where
things are: the heading and the question mark of a card inside it and not on its border, the
star and the bin on one axis, the section menu drawn and every entry reachable, the masthead
still holding the instance's name and logo at the foot of the page, a click on the fixed front
of an address putting the cursor in the box, a service another row uses greyed out, and a map
pin that asks before it is turned on.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.signing_in import sign_in

from .selects import drawn
from .test_accessibility import axe_source, describe, violations_on  # noqa: F401
from .test_row_removal import details  # noqa: F401

pytestmark = pytest.mark.e2e

#: The seven cards that used to draw their heading on the border, by their id.
FIELDSET_CARDS = (
    "section-messaging",
    "section-phones",
    "section-links-social",
    "section-links-repository",
    "section-links-website",
    "section-addresses",
    "section-identifiers",
)

CENTRES = """(block) => {
  const centre = (el) => {
    const r = el.getBoundingClientRect();
    return r.top + r.height / 2 + window.scrollY;
  };
  return [...block.querySelectorAll('li')].filter((li) => li.offsetParent !== null).map((li) => {
    const star = li.querySelector('[data-row-star] .primary-choice, [data-row-actions] .primary-choice');
    const bin = li.querySelector('[data-remove-trigger]');
    return star && bin ? { star: centre(star), bin: centre(bin) } : null;
  }).filter(Boolean);
}"""


@pytest.fixture
def phone(page: Page):
    page.set_viewport_size({"width": 360, "height": 740})
    return page


def test_on_a_phone_the_star_and_the_bin_of_a_row_are_level(phone, live_server, details):
    """On the line they share, at 360 pixels, in every block that has both (#931)."""
    sign_in(phone, live_server.url, "/accounts/profile/")
    found = 0
    for block_id in FIELDSET_CARDS:
        block = phone.locator(f"#{block_id}")
        if not block.count():
            continue
        for centres in block.evaluate(CENTRES):
            found += 1
            assert abs(centres["star"] - centres["bin"]) <= 1, (block_id, centres)
    assert found >= 5, "the fixture holds a row in each of five blocks"


def test_the_star_and_the_bin_stay_level_beside_the_recovery_choice(
    phone, live_server, details, monkeypatch
):
    """A telephone row whose star is beside the choice of the number that gets the person
    back in is the tall one: its wrapper holds more than the star (#931)."""
    monkeypatch.setattr("postulo.core.phone_numbers.can_be_chosen_here", lambda: True)
    sign_in(phone, live_server.url, "/accounts/profile/")
    block = phone.locator("#section-phones")
    expect(block.locator("[data-recovery-choice]").first).to_be_visible()
    rows = block.evaluate(CENTRES)
    assert rows
    for centres in rows:
        assert abs(centres["star"] - centres["bin"]) <= 1, centres


def test_scrolled_to_the_foot_the_masthead_still_names_the_instance(phone, live_server, details):
    """The wordmark is the instance's name and logo at every scroll position (#930)."""
    sign_in(phone, live_server.url, "/accounts/profile/")
    wordmark = phone.locator("[data-wordmark]")
    name = wordmark.inner_text().strip()
    assert name
    phone.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
    phone.wait_for_timeout(400)
    expect(wordmark).to_be_visible()
    assert wordmark.inner_text().strip() == name
    expect(wordmark.locator("img")).to_be_visible()
    assert phone.evaluate("() => document.querySelector('[data-wordmark] img').naturalWidth") > 0


@pytest.mark.parametrize("path", ["/accounts/profile/", "/career/", "/settings/language/"])
def test_the_section_menu_is_a_strip_under_the_heading_on_a_phone(
    phone, live_server, details, path
):
    """Below `lg` the menu is drawn, scrolls sideways, and every entry can be reached (#929)."""
    sign_in(phone, live_server.url, path)
    nav = phone.locator("main nav[aria-label], aside nav[aria-label]").first
    expect(nav).to_be_visible()
    box = nav.bounding_box()
    assert box and box["height"] > 20 and box["width"] > 100, box
    links = nav.locator("a")
    for index in range(links.count()):
        links.nth(index).scroll_into_view_if_needed()
        expect(links.nth(index)).to_be_visible()
    assert phone.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth + 1")


@pytest.mark.parametrize("width", [360, 1280])
def test_every_card_draws_its_heading_and_mark_inside_it(page: Page, live_server, details, width):
    """The seven fieldset cards put their heading and question mark in the card, below its
    top border, as the five others do (#919)."""
    page.set_viewport_size({"width": width, "height": 900})
    sign_in(page, live_server.url, "/accounts/profile/")
    for block_id in FIELDSET_CARDS:
        card = page.locator(f"#{block_id}")
        if not card.count():
            continue
        box = card.bounding_box()
        legend = card.locator(":scope > legend").bounding_box()
        assert box and legend
        assert legend["y"] >= box["y"] + 8, (block_id, legend, box)
        mark = card.locator(":scope > [data-help-mark]")
        if mark.count():
            where = mark.bounding_box()
            assert where["y"] >= box["y"] + 8, (block_id, where, box)
            assert where["y"] <= legend["y"] + legend["height"], (block_id, where, legend)
            assert where["x"] + where["width"] <= box["x"] + box["width"] - 8
        # Named by its legend still.
        assert card.evaluate("(el) => el.querySelector(':scope > legend').textContent.trim()")


def test_every_entry_of_the_section_menu_has_an_icon(page: Page, live_server, details):
    page.set_viewport_size({"width": 1280, "height": 900})
    sign_in(page, live_server.url, "/accounts/profile/")
    links = page.locator("nav[data-vertical-tabs] a")
    assert links.count() >= 9
    for index in range(links.count()):
        expect(links.nth(index).locator("svg")).to_have_count(1)
    expect(page.locator('nav[data-vertical-tabs] a[href="#section-cv-photo"]')).to_have_count(1)


def test_an_identifier_can_be_starred_and_the_star_moves_when_the_row_goes(
    page: Page, live_server, details
):
    from postulo.accounts.models import PersonIdentifier

    PersonIdentifier.objects.create(
        profile=details["identifier"].profile, scheme="orcid", value="0000-0002-1825-0097"
    )
    page.set_viewport_size({"width": 1280, "height": 900})
    sign_in(page, live_server.url, "/accounts/profile/")
    block = page.locator("#section-identifiers")
    stars = block.locator("input[type=radio][name=identifiers-primary]")
    assert stars.count() == 3  # two saved rows and the empty one
    assert PersonIdentifier.objects.filter(is_primary=True).count() == 1
    other = next(r for r in PersonIdentifier.objects.all() if not r.is_primary)
    block.locator("li", has=page.locator(f"input[value='{other.pk}'][name$='-id']")).locator(
        ".primary-choice"
    ).click()
    page.get_by_role("button", name="Save", exact=True).click()
    other.refresh_from_db()
    assert other.is_primary
    assert PersonIdentifier.objects.filter(is_primary=True).count() == 1


def test_clicking_the_front_of_an_address_puts_the_cursor_in_the_name(
    page: Page, live_server, details
):
    page.set_viewport_size({"width": 1280, "height": 900})
    sign_in(page, live_server.url, "/accounts/profile/")
    row = page.locator("#section-links-social [data-link-row]").last
    service = row.locator("select[data-service-select]")
    service.select_option("linkedin")
    prefix = row.locator("[data-address-group] > span").first
    expect(prefix).to_be_visible()
    prefix.click()
    expect(row.locator("[data-address-box]")).to_be_focused()


def test_a_service_another_row_uses_is_greyed_out(page: Page, live_server, details, applicant):
    from postulo.core.models import WebLink

    WebLink.objects.create(
        owner=applicant,
        holder=applicant.profile,
        kind=WebLink.Kind.SOCIAL,
        service="linkedin",
        url="https://www.linkedin.com/in/alex",
    )
    page.set_viewport_size({"width": 1280, "height": 900})
    sign_in(page, live_server.url, "/accounts/profile/")
    new = page.locator("#section-links-social [data-link-row]").last
    option = new.locator("select[data-service-select] option[value=linkedin]")
    assert option.evaluate("(o) => o.disabled")
    assert not new.locator("select[data-service-select] option[value=mastodon]").evaluate(
        "(o) => o.disabled"
    )
    button = drawn(new.locator("select[data-service-select]"))
    button.click()
    row = page.locator("[role=option]", has_text="LinkedIn")
    expect(row).to_have_attribute("aria-disabled", "true")


def test_the_map_pin_asks_before_it_is_turned_on(page: Page, live_server, details):
    page.set_viewport_size({"width": 1280, "height": 900})
    sign_in(page, live_server.url, "/accounts/profile/")
    pin = page.locator("#section-addresses .map-toggle").first
    box = page.locator("#section-addresses .map-toggle input").first
    expect(pin).to_be_visible()
    pin.click()
    dialog = page.locator("#map-confirm")
    expect(dialog.get_by_role("button", name="Show on the map")).to_be_visible()
    expect(box).not_to_be_checked()
    page.keyboard.press("Escape")
    expect(box).not_to_be_checked()
    expect(box).to_be_focused()
    pin.click()
    dialog.get_by_role("button", name="Show on the map").click()
    expect(box).to_be_checked()
    pin.click()  # turning it off asks nothing
    expect(box).not_to_be_checked()


@pytest.mark.parametrize("width", [390, 1280])
def test_the_heading_has_its_question_mark_and_no_sentence_under_it(
    page: Page, live_server, details, width
):
    """A question mark follows the H1 at every width, its 24px target in sight, with the
    page's sentence as its tooltip and no paragraph under the heading (#918)."""
    page.set_viewport_size({"width": width, "height": 900})
    sign_in(page, live_server.url, "/accounts/profile/")
    heading = page.locator("main h1").first
    mark = page.locator(".page-title-help [data-help-mark]")
    expect(heading).to_be_visible()
    expect(mark).to_be_visible()
    box, target = heading.bounding_box(), mark.bounding_box()
    assert target["width"] >= 24 and target["height"] >= 24, target
    assert abs((target["y"] + target["height"] / 2) - (box["y"] + box["height"] / 2)) <= 12
    assert target["x"] + target["width"] <= width
    # With scripts on the sentence is the tooltip: nothing is drawn under the heading.
    expect(page.locator(".page-title-help [data-help-summary]")).not_to_be_visible()
    mark.focus()
    expect(page.locator(".page-title-help [data-help-summary]")).to_be_visible()
    assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth + 1")
    for card in ("section-cv-photo", "section-personal", "section-messaging"):
        expect(page.locator(f"#{card} [data-help-mark]").first).to_be_visible()
