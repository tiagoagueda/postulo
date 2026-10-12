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
    from postulo.jobs.models import Company, LocationSource

    for name, place, lat, lon in (
        ("Aveiro Labs", "Aveiro", 40.64, -8.65),
        ("Lisbon Works", "Lisbon", 38.72, -9.14),
        ("Lisbon Two", "Lisbon", 38.72, -9.14),
    ):
        company = Company.objects.create(owner=applicant, name=name, location=place)
        Company.objects.filter(pk=company.pk).update(
            location_lat=lat,
            location_lon=lon,
            location_resolved_from=place,
            location_resolved_by=LocationSource.MANUAL,
        )
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
    expect(page.locator("path.map-dot")).to_have_count(2)
    expect(page.locator(".leaflet-control-attribution")).to_contain_text(
        "© OpenStreetMap contributors"
    )
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
    expect(page.locator("path.map-dot")).to_have_count(2)
    page.wait_for_load_state("networkidle")
    assert tiles == []
    assert breaches == []


def test_a_dot_opens_the_place_with_its_companies_as_links(live_server, page: Page, placed):
    watch(page)
    sign_in(page, live_server.url, "/jobs/companies/map/")

    page.locator("path.map-dot").last.click(force=True)

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
    expect(page.locator(".leaflet-control-attribution")).to_be_visible()

    inside = page.evaluate(
        """() => {
          const box = document.querySelector('[data-map]').getBoundingClientRect();
          return [...document.querySelectorAll('.leaflet-control, .leaflet-control a')]
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


def test_near_me_says_so_when_the_browser_will_not_tell(live_server, page: Page, placed):
    watch(page)
    sign_in(page, live_server.url, "/jobs/companies/map/")

    page.get_by_role("button", name="Near me").click()

    expect(page.locator("[data-map-status]")).to_contain_text("The browser")
    expect(page.locator("path.map-here")).to_have_count(0)
