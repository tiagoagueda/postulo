"""Where the companies' map takes its tiles, and what the map page then allows (#898).

OpenStreetMap's public tiles are the default since the maintainer reversed #700's decision
on 2026-10-12. The promises held here: the content security policy opens on the map page
alone and only to the configured origin; *none* fetches nothing; a custom address is checked
before it is stored; the environment pins what it sets; an administrator's typed credit is
plain text.
"""

from __future__ import annotations

import re

import pytest
from django.core.management import call_command
from django.core.management.base import SystemCheckError
from django.urls import reverse

from postulo.core.models import SiteSettings
from postulo.jobs import tiles
from postulo.jobs.models import Company


@pytest.fixture
def staff(user):
    user.is_staff = True
    user.save(update_fields=["is_staff"])
    return user


def choose(**fields) -> None:
    row = SiteSettings.get()
    for name, value in fields.items():
        setattr(row, name, value)
    row.save()


def map_page(client, user):
    client.force_login(user)
    Company.objects.get_or_create(owner=user, name="One", defaults={"location": "Lisbon"})
    return client.get(reverse("jobs:company_map"))


def images_allowed(response) -> str:
    header = response.headers["Content-Security-Policy"]
    return next(part for part in header.split(";") if part.strip().startswith("img-src"))


def test_openstreetmap_is_the_default(db):
    current = tiles.current()

    assert current.source == tiles.OSM
    assert current.url == "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
    assert current.origin == "https://tile.openstreetmap.org"
    assert current.attribution == "© OpenStreetMap contributors"


def test_the_map_page_alone_may_show_the_tile_servers_images(client, user):
    on_the_map = images_allowed(map_page(client, user))
    elsewhere = images_allowed(client.get(reverse("jobs:company_list")))

    assert "https://tile.openstreetmap.org" in on_the_map
    assert "openstreetmap" not in elsewhere
    assert elsewhere.split() == ["img-src", "'self'", "data:"]


def test_the_rest_of_the_map_pages_policy_is_the_sites(client, user):
    header = map_page(client, user).headers["Content-Security-Policy"]

    assert "script-src 'self';" in header + ";"
    assert "connect-src 'self'" in header
    assert "unsafe" not in header


def test_the_page_hands_leaflet_the_tiles_and_their_credit(client, user):
    html = map_page(client, user).content.decode()

    assert 'data-map-tiles="https://tile.openstreetmap.org/{z}/{x}/{y}.png"' in html
    credit = re.search(r"<p[^>]*data-map-tile-credit[^>]*>.*?</p>", html, re.S)[0]
    assert "hidden" in credit.split(">")[0], "shown by the script once the map is drawn"
    assert 'href="https://www.openstreetmap.org/copyright"' in credit
    assert "© OpenStreetMap contributors" in credit and ">Leaflet</a>" in credit
    footer = re.search(r"<footer[^>]*map-footer.*?</footer>", html, re.S)[0]
    assert "data-map-tile-credit" in footer, "the credit is the end of the footer's line"
    assert "data-map-outline" not in html, "the outline is for a map without tiles"


def test_no_tiles_fetches_nothing_and_draws_the_outline(client, user):
    choose(map_tiles=tiles.NONE)

    response = map_page(client, user)
    html = response.content.decode()

    assert images_allowed(response).split() == ["img-src", "'self'", "data:"]
    assert 'data-map-tiles=""' in html
    assert "data-map-outline=" in html
    assert len(response.context["outline"]) > 100, "the world's rings, from the SVG's path"


def test_the_outline_is_the_svgs_own_drawing_turned_back_into_degrees():
    from postulo.jobs import outline

    rings = outline.rings()
    # The path starts "M358.1 107.5": x - 180 is the longitude, 90 - y the latitude.
    assert rings[0][0] == (-17.5, 178.1)
    assert all(-90 <= lat <= 90 and -180 <= lon <= 180 for ring in rings for lat, lon in ring)


def test_a_tile_server_of_ones_own_is_the_only_other_origin(client, user):
    choose(
        map_tiles=tiles.CUSTOM,
        map_tile_url="https://{s}.tiles.example.org/{z}/{x}/{y}.png",
        map_tile_attribution="Example tiles",
    )

    response = map_page(client, user)

    allowed = images_allowed(response)
    assert "https://*.tiles.example.org" in allowed
    assert "openstreetmap" not in allowed


def test_tiles_on_this_instances_own_origin_widen_nothing(client, user):
    choose(map_tiles=tiles.CUSTOM, map_tile_url="/tiles/{z}/{x}/{y}.png", map_tile_attribution="Us")

    response = map_page(client, user)

    assert images_allowed(response).split() == ["img-src", "'self'", "data:"]
    assert 'data-map-tiles="/tiles/{z}/{x}/{y}.png"' in response.content.decode()


def test_a_typed_credit_is_plain_text(client, user):
    choose(
        map_tiles=tiles.CUSTOM,
        map_tile_url="https://tiles.example.org/{z}/{x}/{y}.png",
        map_tile_attribution='<img src=x onerror="alert(1)">',
    )

    html = map_page(client, user).content.decode()

    assert '<img src=x onerror="alert(1)">' not in html
    assert "&lt;img src=x onerror=" in html


@pytest.mark.parametrize(
    "url",
    [
        "",
        "http://tiles.example.org/{z}/{x}/{y}.png",
        "https://tiles.example.org/tile.png",
        "//tiles.example.org/{z}/{x}/{y}.png",
        'https://tiles.example.org/{z}/{x}/{y}.png" onload="x',
        "javascript:alert(1)//{z}{x}{y}",
        "https://{z}.example.org/{z}/{x}/{y}.png",
    ],
)
def test_an_address_that_is_not_a_tile_template_is_refused(url):
    assert tiles.url_problem(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
        "https://{s}.tiles.example.org:8443/{z}/{x}/{y}.png?key=abc",
        "/tiles/{z}/{x}/{y}.png",
    ],
)
def test_a_tile_template_is_accepted(url):
    assert tiles.url_problem(url) == ""


def test_a_stored_address_the_map_cannot_use_draws_no_tiles(db):
    """Only the environment can put one there; the map fetches nothing rather than guess."""
    choose(map_tiles=tiles.CUSTOM, map_tile_url="http://insecure.example/{z}/{x}/{y}.png")

    assert tiles.current().source == tiles.NONE


def test_the_settings_page_is_an_administrators(client, user, staff):
    client.force_login(user)
    assert client.get(reverse("server:maps")).status_code == 200

    user.is_staff = False
    user.save(update_fields=["is_staff"])
    assert client.get(reverse("server:maps")).status_code in (302, 403, 404)


def test_the_settings_page_stores_a_checked_address(client, staff):
    client.force_login(staff)

    refused = client.post(
        reverse("server:maps"),
        {"map_tiles": "custom", "map_tile_url": "http://x/{z}/{x}/{y}", "map_tile_attribution": ""},
    )
    assert refused.status_code == 200
    assert SiteSettings.get().map_tiles == ""

    kept = client.post(
        reverse("server:maps"),
        {
            "map_tiles": "custom",
            "map_tile_url": "https://tiles.example.org/{z}/{x}/{y}.png",
            "map_tile_attribution": "Example",
        },
    )
    assert kept.status_code == 302
    row = SiteSettings.get()
    assert (row.map_tiles, row.map_tile_url, row.map_tile_attribution) == (
        "custom",
        "https://tiles.example.org/{z}/{x}/{y}.png",
        "Example",
    )
    assert tiles.current().origin == "https://tiles.example.org"


def test_the_settings_page_names_what_openstreetmap_learns(client, staff):
    client.force_login(staff)

    html = client.get(reverse("server:maps")).content.decode()

    assert "learns that browser's address and the area it looks at" in html


def test_the_environment_pins_the_choice(client, staff, monkeypatch, settings):
    monkeypatch.setenv("POSTULO_MAP_TILES", "none")
    settings.POSTULO_MAP_TILES = "none"
    choose(map_tiles=tiles.OSM)

    assert tiles.current().source == tiles.NONE
    client.force_login(staff)
    client.post(reverse("server:maps"), {"map_tiles": "osm"})
    assert tiles.current().source == tiles.NONE, "a pinned field is never written"
    assert "POSTULO_MAP_TILES" in client.get(reverse("server:maps")).content.decode()


def test_the_plugin_list_links_to_the_settings(client, staff):
    client.force_login(staff)

    html = client.get(reverse("server:plugins")).content.decode()

    assert reverse("server:maps") in html


def test_a_bad_address_in_the_environment_is_named_by_the_check(settings):
    settings.POSTULO_MAP_TILES = "custom"
    settings.POSTULO_MAP_TILE_URL = "http://tiles.example.org/{z}/{x}/{y}.png"

    with pytest.raises(SystemCheckError, match=r"postulo.E042"):
        call_command("check", tags=["postulo"])


def test_a_source_that_is_not_one_of_the_three_is_named_by_the_check(settings):
    settings.POSTULO_MAP_TILES = "google"

    with pytest.raises(SystemCheckError, match="POSTULO_MAP_TILES"):
        call_command("check", tags=["postulo"])
