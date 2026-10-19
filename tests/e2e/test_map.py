"""The companies' map in a browser: Leaflet draws the list, under the page's own policy (#898).

No request in these tests reaches OpenStreetMap: the browser's requests for its tiles are
answered here with a one-pixel image, and what the browser *sent* is what is checked.
"""

from __future__ import annotations

import base64

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.signing_in import sign_in

pytestmark = pytest.mark.e2e

#: The smallest PNG there is, so a tile request has an answer without leaving the machine.
PIXEL = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)


@pytest.fixture
def placed(applicant):
    from postulo.jobs import placing
    from postulo.jobs.models import Company

    for name, place, lat, lon in (
        ("Aveiro Labs", "Aveiro", 40.64, -8.65),
        ("Lisbon Works", "Lisbon", 38.72, -9.14),
        ("Lisbon Two", "Lisbon", 38.72, -9.14),
    ):
        company = Company.objects.create(owner=applicant, name=name, location=f"{place}, PT")
        placing.place_by_hand(company.postal_addresses.get(), lat, lon)
    return applicant


def watch(page: Page) -> tuple[list[str], list[dict]]:
    breaches: list[str] = []
    tiles: list[dict] = []
    page.on(
        "console",
        lambda message: (
            breaches.append(message.text) if "Content Security Policy" in message.text else None
        ),
    )

    def answer(route):
        tiles.append(route.request.headers)
        route.fulfill(status=200, content_type="image/png", body=PIXEL)

    page.route("https://tile.openstreetmap.org/**", answer)
    return breaches, tiles


def test_leaflet_draws_a_dot_per_place_over_openstreetmap(live_server, page: Page, placed):
    breaches, tiles = watch(page)

    sign_in(page, live_server.url, "/jobs/companies/map/")

    expect(page.locator("[data-map].leaflet-container")).to_be_visible()
    # Aveiro alone, and Lisbon's two companies at one spot gathered into a cluster of two (#703).
    expect(page.locator(".map-pin")).to_have_count(1)
    expect(page.locator(".map-cluster")).to_have_text("2Companies here: 2")
    credit = page.locator("footer [data-map-tile-credit]")
    expect(credit).to_contain_text("© OpenStreetMap contributors")
    expect(credit.get_by_role("link", name="Leaflet")).to_be_visible()
    expect(page.locator(".leaflet-control-attribution")).to_have_count(0)
    page.wait_for_load_state("networkidle")
    assert tiles, "the browser asked for tiles"
    referers = {headers.get("referer", "") for headers in tiles}
    assert referers == {f"{live_server.url}/"}, "the instance's origin, never the page's path"
    assert breaches == []


def test_with_no_tiles_the_outline_is_drawn_and_nothing_is_fetched(live_server, page: Page, placed):
    from postulo.core.models import SiteSettings

    row = SiteSettings.get()
    row.map_tiles = "none"
    row.save()
    breaches, tiles = watch(page)

    sign_in(page, live_server.url, "/jobs/companies/map/")

    expect(page.locator("path.map-outline")).to_have_count(1)
    credit = page.locator("footer [data-map-tile-credit]")
    expect(credit).to_have_text("Leaflet")  # no tiles, so nobody else to credit
    expect(page.locator(".map-pin, .map-cluster")).to_have_count(2)
    page.wait_for_load_state("networkidle")
    assert tiles == []
    assert breaches == []


def test_a_dot_opens_the_place_with_its_companies_as_links(live_server, page: Page, placed):
    watch(page)
    sign_in(page, live_server.url, "/jobs/companies/map/")

    page.locator(".map-pin").first.click()

    popup = page.locator(".leaflet-popup-content")
    expect(popup).to_be_visible()
    expect(popup.get_by_role("link")).not_to_have_count(0)


def test_the_credit_stays_inside_the_map_on_a_phone_with_wider_text(
    live_server, page: Page, placed
):
    """The text spacing walk leaves the map's viewport out, because tiles off to either
    side are what it clips; this holds the words over it inside the box instead."""
    from .test_text_spacing import ADOPT, TEXT_SPACING

    watch(page)
    page.set_viewport_size({"width": 360, "height": 740})
    sign_in(page, live_server.url, "/jobs/companies/map/")
    page.evaluate(ADOPT, TEXT_SPACING)
    expect(page.locator("[data-map-tile-credit]")).to_be_visible()

    inside = page.evaluate(
        """() => {
          const box = document.querySelector('[data-map]').getBoundingClientRect();
          return [...document.querySelectorAll(
              '.leaflet-control, .leaflet-control a, [data-map-tile-credit] a')]
            .map(el => el.getBoundingClientRect())
            .every(r => r.left >= box.left - 1 && r.right <= box.right + 1
                        && r.top >= box.top - 1 && r.bottom <= box.bottom + 1);
        }"""
    )
    assert inside, "a control or the credit is cut off by the map's edge"


def test_near_me_moves_the_map_to_the_browser_when_pressed(live_server, page: Page, placed):
    watch(page)
    page.context.grant_permissions(["geolocation"])
    page.context.set_geolocation({"latitude": 41.15, "longitude": -8.61})
    sign_in(page, live_server.url, "/jobs/companies/map/")

    expect(page.locator("path.map-here")).to_have_count(0)
    page.get_by_role("button", name="Near me").click()

    expect(page.locator("path.map-here")).to_have_count(1)
    assert page.locator("[data-map-status]").inner_text() == ""


def test_the_you_are_here_tooltip_is_text_even_if_a_translation_holds_markup(
    live_server, page: Page, placed
):
    """Leaflet writes a string tooltip with innerHTML; the words must arrive as text (#983)."""
    watch(page)
    page.context.grant_permissions(["geolocation"])
    page.context.set_geolocation({"latitude": 41.15, "longitude": -8.61})
    sign_in(page, live_server.url, "/jobs/companies/map/")
    page.evaluate(
        "document.querySelector('[data-map-here]').setAttribute('data-map-here', '<b>here</b>')"
    )

    page.get_by_role("button", name="Near me").click()
    page.locator("path.map-here").hover()

    tooltip = page.locator(".leaflet-tooltip")
    expect(tooltip).to_have_text("<b>here</b>")
    expect(tooltip.locator("b")).to_have_count(0)


def test_near_me_says_so_when_the_browser_will_not_tell(live_server, page: Page, placed):
    watch(page)
    sign_in(page, live_server.url, "/jobs/companies/map/")

    page.get_by_role("button", name="Near me").click()

    expect(page.locator("[data-map-status]")).to_contain_text("The browser")
    expect(page.locator("path.map-here")).to_have_count(0)


def test_a_click_on_the_map_places_an_address_by_hand(live_server, page: Page, placed):
    """#702: the pin dropped on the small map fills the two numbers, which are the form."""
    from postulo.jobs.models import Company

    address = Company.objects.get(owner=placed, name="Aveiro Labs").postal_addresses.get()
    watch(page)
    sign_in(page, live_server.url, f"/jobs/addresses/{address.pk}/place/")

    canvas = page.locator("[data-map-pick].leaflet-container")
    expect(canvas).to_be_visible()
    box = canvas.bounding_box()
    page.mouse.click(box["x"] + box["width"] / 3, box["y"] + box["height"] / 3)
    expect(page.locator("#latitude")).not_to_have_value("40.640000")
    page.get_by_role("button", name="Place it here").click()

    address.refresh_from_db()
    assert address.placed_by == "manual"
    assert (address.latitude, address.longitude) != (40.64, -8.65)


# ------------------------------------- the buttons and the credit in the end corner (#909)


def layout(page: Page) -> dict:
    """Where everything over the map sits, against the map, the footer's line and each other."""
    return page.evaluate(
        """() => {
          const box = (el) => el.getBoundingClientRect();
          const map = box(document.querySelector('[data-map]'));
          const footer = box(document.querySelector('.map-footer'));
          const heading = box(document.querySelector('[data-map-heading]'));
          const drawer = box(document.querySelector('[data-map-drawer]'));
          const table = box(document.querySelector('[data-map-table]'));
          const middle = map.left + map.width / 2;
          const boxes = [...document.querySelectorAll(
              '.leaflet-control-zoom, .leaflet-control:has(.map-locate)')].map(box);
          return {
            count: boxes.length,
            start: boxes.every(r => r.left < middle),
            end: boxes.every(r => r.right > middle),
            above_footer: boxes.every(r => r.bottom <= footer.top + 1),
            footer_end: footer.right > middle && footer.bottom > map.bottom - 24,
            footer_start: footer.left < middle && footer.bottom > map.bottom - 24,
            drawer_under_heading: drawer.top >= heading.bottom - 1,
            drawer_start: drawer.left < middle,
            table_top_end: table.right > middle && table.top < map.top + 24,
            heading_clear_of_table: heading.right <= table.left + 1,
          };
        }"""
    )


@pytest.mark.parametrize("width", [1280, 390])
def test_the_buttons_and_credit_are_in_the_end_corner_and_the_list_under_the_heading(
    live_server, page: Page, placed, width
):
    watch(page)
    page.set_viewport_size({"width": width, "height": 800})
    sign_in(page, live_server.url, "/jobs/companies/map/")
    expect(page.locator(".leaflet-control-zoom")).to_be_visible()

    where = layout(page)
    assert where["count"] == 2 and where["end"] and where["above_footer"], where
    assert where["footer_end"] and where["table_top_end"] and where["heading_clear_of_table"], where
    assert where["drawer_under_heading"] and where["drawer_start"], where
    expect(page.locator(".leaflet-bottom.leaflet-left .leaflet-control")).to_have_count(0)
    expect(page.locator("[data-map-drawer]")).to_have_js_property("open", width > 640)


def test_right_to_left_the_buttons_follow_the_footer_to_the_left(
    live_server, page: Page, placed, applicant
):
    profile = applicant.profile
    profile.language = "ar"
    profile.save(update_fields=["language"])
    watch(page)
    page.set_viewport_size({"width": 1280, "height": 800})
    sign_in(page, live_server.url, "/jobs/companies/map/")
    expect(page.locator(".leaflet-control-zoom")).to_be_visible()

    where = layout(page)
    assert where["count"] == 2 and where["start"] and where["above_footer"], where
    assert where["footer_start"], where


@pytest.mark.parametrize(
    ("selector", "words"),
    [
        (".leaflet-control-zoom-in", "Zoom in"),
        (".leaflet-control-zoom-out", "Zoom out"),
        (".map-locate", "Near me"),
        (".map-footer [data-source]", "Source code"),
        (".map-footer [data-help]", "Help"),
    ],
)
def test_each_icon_is_named_by_the_tooltip_it_shows(
    live_server, page: Page, placed, selector, words
):
    """#909: the page's own tooltip on hover, Basecoat's look, and no browser `title`."""
    watch(page)
    page.set_viewport_size({"width": 1280, "height": 800})
    sign_in(page, live_server.url, "/jobs/companies/map/")
    expect(page.locator(".leaflet-control-zoom")).to_be_visible()
    control = page.locator(selector)
    expect(control).to_have_accessible_name(words)
    assert control.get_attribute("title") is None

    control.hover()

    tip = page.locator("[data-tooltip][popover]:popover-open")
    expect(tip).to_have_count(1)
    expect(tip).to_have_text(words)


def test_the_footer_over_the_map_has_no_motto_and_no_words_beside_its_icons(
    live_server, page: Page, placed
):
    watch(page)
    page.set_viewport_size({"width": 1280, "height": 800})
    sign_in(page, live_server.url, "/jobs/companies/map/")
    expect(page.locator("[data-map-tile-credit]")).to_be_visible()

    footer = page.locator(".map-footer")
    expect(footer.locator("[data-motto]")).to_have_count(0)
    expect(footer.get_by_role("link", name="Powered by Postulo")).to_be_visible()
    expect(footer.get_by_text("Source code")).to_be_hidden()
    expect(footer.get_by_text("Help", exact=True)).to_be_hidden()
    expect(footer.locator('[data-map-tile-credit] [data-icon="dot"]')).to_be_visible()
    assert page.locator("[data-map-heading]").evaluate(
        "el => getComputedStyle(el).backgroundColor === 'rgba(0, 0, 0, 0)'"
        " && getComputedStyle(el).borderTopWidth === '0px'"
    )
