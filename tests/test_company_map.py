"""The companies' map: the list is the page, and the map draws the same list (#108, #898).

A map is the classic screen-reader failure, so these tests hold the promise the screen
makes: everything the drawing says is text on the page, and what Leaflet draws is handed to
it as data. Where its tiles come from, and what the page's policy then allows, is held in
`tests/test_map_tiles.py`.
"""

from __future__ import annotations

import re

import pytest
from django.urls import reverse

from postulo.jobs import places
from postulo.jobs.models import Company


@pytest.fixture(autouse=True)
def fresh_loader():
    for cached in (places._index, places._countries):
        cached.cache_clear()
    yield
    for cached in (places._index, places._countries):
        cached.cache_clear()


def page(client, user):
    client.force_login(user)
    return client.get(reverse("jobs:company_map"))


def city_table(*cities) -> str:
    """A table of the cities the test needs, in the order GeoNames publishes it."""
    rows = []
    for name, lat, lon, country, population in cities:
        rows.append(
            "\t".join(
                [
                    "1",
                    name,
                    name,
                    "",
                    lat,
                    lon,
                    "P",
                    "PPLC",
                    country,
                    country,
                    "",
                    "",
                    "",
                    "",
                    str(population),
                    "0",
                    "",
                    "UTC",
                    "2026-01-01",
                ]
            )
        )
    return "\n".join(rows) + "\n"


LISBON = ("Lisbon", "38.72509", "-9.1498", "PT", 517802)
BERLIN = ("Berlin", "52.52437", "13.41053", "DE", 3644832)


def test_the_places_and_the_companies_at_them_are_the_page(client, user, other_user):
    """Every place, company and count is text before any of it is drawn (#108)."""
    Company.objects.create(owner=user, name="One", location="Lisbon")
    Company.objects.create(owner=user, name="Two", location="Lisbon")
    Company.objects.create(owner=user, name="Three", location="Berlin")
    Company.objects.create(owner=other_user, name="Theirs", location="Lisbon")

    html = page(client, user).content.decode()

    assert "Lisbon" in html and "Berlin" in html
    assert "One" in html and "Two" in html and "Three" in html
    assert "Theirs" not in html, "another person's companies are not on this map"


def test_a_place_with_coordinates_is_drawn(client, user, monkeypatch, tmp_path):
    """The dot is where the data puts it, handed to Leaflet as data (#108, #898)."""
    (tmp_path / places.CITIES_FILE).write_text(city_table(LISBON), encoding="utf-8")
    monkeypatch.setattr(places, "DATA_DIR", tmp_path)
    Company.objects.create(owner=user, name="One", location="Lisbon")

    html = page(client, user).content.decode()

    (drawn,) = page(client, user).context["drawn"]
    assert (drawn["lat"], drawn["lon"]) == (38.72509, -9.1498)
    assert "data-map-places=" in html and "38.72509" in html


def test_a_place_without_coordinates_is_listed_and_not_drawn(client, user, tmp_path, monkeypatch):
    monkeypatch.setattr(places, "DATA_DIR", tmp_path)
    Company.objects.create(owner=user, name="One", location="Remote")

    html = page(client, user).content.decode()

    assert "Remote" in html
    assert "Not on the map" in html
    assert page(client, user).context["drawn"] == [], "listed, and nothing handed to the map"


def test_the_dots_are_proportional_to_the_companies_at_the_place(
    client, user, monkeypatch, tmp_path
):
    """The question the map answers is where the search is spread: each place counts its
    companies, and the map draws one marker per address, gathered into clusters that count
    companies (#108, #703)."""
    (tmp_path / places.CITIES_FILE).write_text(city_table(LISBON, BERLIN), encoding="utf-8")
    monkeypatch.setattr(places, "DATA_DIR", tmp_path)
    Company.objects.create(owner=user, name="A", location="Lisbon")
    for number in range(2, 7):
        Company.objects.create(owner=user, name=f"B{number}", location="Berlin")

    context = page(client, user).context

    by_label = {entry["label"]: entry for entry in context["places"]}
    assert by_label["Lisbon, Portugal"]["count"] == 1
    assert by_label["Berlin, Germany"]["count"] == 5
    # One marker per address since #703, which the clusters gather and count by company.
    assert len(context["drawn"]) == 6
    assert len({entry["company"] for entry in context["drawn"]}) == 6


def test_the_map_is_drawn_by_leaflet_from_this_instance(client, user):
    """Leaflet and its stylesheet come from Postulo's own origin, on this page alone (#898)."""
    Company.objects.create(owner=user, name="One", location="Lisbon")

    html = page(client, user).content.decode()

    assert "js/vendor/leaflet/leaflet.js" in html
    assert "js/vendor/leaflet/leaflet.css" in html
    assert "unpkg.com" not in html and "cdn" not in html.lower().replace("cdnjs", "")
    client.force_login(user)
    elsewhere = client.get(reverse("jobs:company_list")).content.decode()
    assert "leaflet" not in elsewhere.lower(), "no other page loads it"


def test_a_company_name_reaches_the_map_as_data_only(client, user, monkeypatch, tmp_path):
    """A name with markup in it is escaped into the attribute and never becomes markup."""
    (tmp_path / places.CITIES_FILE).write_text(city_table(LISBON), encoding="utf-8")
    monkeypatch.setattr(places, "DATA_DIR", tmp_path)
    Company.objects.create(owner=user, name='<img src=x onerror="alert(1)">', location="Lisbon")

    html = page(client, user).content.decode()

    assert '<img src=x onerror="alert(1)">' not in html
    assert "&lt;img src=x onerror=" in html


def test_the_empty_page_says_what_to_do_next(client, user):
    html = page(client, user).content.decode()

    assert "No places yet" in html


def test_the_missing_dataset_is_said_plainly(client, user, monkeypatch):
    """Without the tables the map is a list, and the page says why (#108)."""
    monkeypatch.setattr(places, "available", lambda: False)
    Company.objects.create(owner=user, name="One", location="Lisbon")

    html = page(client, user).content.decode()

    assert "fetch_geonames" in html


def address_of(company):
    return company.postal_addresses.get(is_primary=True)


def place_by_hand(client, user, address, **data):
    client.force_login(user)
    return client.post(reverse("jobs:address_place", args=[address.pk]), data)


def test_a_wrong_guess_is_placed_by_hand(client, user, monkeypatch, tmp_path):
    """Somewhere to put an address where it really is: the address's own page, where the
    pin is dropped or the two numbers typed, and the point is kept as a person's (#702)."""
    with_cities(monkeypatch, tmp_path, LISBON, BERLIN)
    company = Company.objects.create(owner=user, name="One", location="Lisbon")
    address = address_of(company)

    response = place_by_hand(client, user, address, latitude="52.52437", longitude="13.41053")

    assert response.status_code == 302
    address.refresh_from_db()
    assert address.placed_by == "manual"
    assert (address.latitude, address.longitude) == (52.52437, 13.41053)


def test_a_point_off_the_globe_is_refused(client, user, monkeypatch, tmp_path):
    with_cities(monkeypatch, tmp_path, LISBON)
    company = Company.objects.create(owner=user, name="One", location="Lisbon")
    address = address_of(company)

    place_by_hand(client, user, address, latitude="95", longitude="0")
    place_by_hand(client, user, address, latitude="north", longitude="0")

    address.refresh_from_db()
    assert address.placed_by == "town"


def test_taking_a_hand_placed_point_off_places_it_from_its_town(
    client, user, monkeypatch, tmp_path
):
    with_cities(monkeypatch, tmp_path, LISBON, BERLIN)
    company = Company.objects.create(owner=user, name="One", location="Lisbon")
    address = address_of(company)
    place_by_hand(client, user, address, latitude="52.52437", longitude="13.41053")

    place_by_hand(client, user, address, take_off="1")

    address.refresh_from_db()
    assert address.placed_by == "town"
    assert (address.latitude, address.longitude) == (38.72509, -9.1498)


def test_another_accounts_address_cannot_be_placed(client, user, other_user):
    company = Company.objects.create(owner=other_user, name="Theirs", location="Lisbon")
    address = address_of(company)
    client.force_login(user)

    assert client.get(reverse("jobs:address_place", args=[address.pk])).status_code == 404
    assert place_by_hand(client, user, address, latitude="1", longitude="1").status_code == 404


def test_the_place_page_draws_over_the_tiles_and_allows_only_them(client, user, openstreetmap):
    company = Company.objects.create(owner=user, name="One", location="Lisbon")
    client.force_login(user)

    response = client.get(reverse("jobs:address_place", args=[address_of(company).pk]))

    assert response.status_code == 200
    assert "tile.openstreetmap.org" in response.headers["Content-Security-Policy"]
    assert "data-map-pick" in response.content.decode()


def switch_off(person) -> None:
    from postulo.plugins import policy
    from postulo.plugins.maps import MAPS
    from postulo.plugins.models import PluginPolicy

    PluginPolicy.objects.update_or_create(
        plugin=MAPS, person=person, defaults={"state": PluginPolicy.State.FORCED_OFF}
    )
    # A decision is memoised for one request; outside one, the next request is this.
    policy.forget_decisions()


def switch_on(person) -> None:
    from postulo.plugins import policy
    from postulo.plugins.maps import MAPS
    from postulo.plugins.models import PluginPolicy

    PluginPolicy.objects.filter(plugin=MAPS, person=person).delete()
    policy.forget_decisions()


def with_cities(monkeypatch, tmp_path, *cities) -> None:
    (tmp_path / places.CITIES_FILE).write_text(city_table(*cities), encoding="utf-8")
    monkeypatch.setattr(places, "DATA_DIR", tmp_path)


def test_the_map_is_a_feature_plugin_and_on_by_default(client, user):
    from postulo.plugins import registry
    from postulo.plugins.base import manifest_of
    from postulo.plugins.maps import MAPS

    names = {manifest_of(cls()).name: manifest_of(cls()) for cls in registry.builtins()["feature"]}

    assert names[MAPS].kind == "feature"
    assert page(client, user).status_code == 200


def test_off_the_map_page_is_not_found(client, user):
    switch_off(user)

    assert page(client, user).status_code == 404


def test_off_for_one_person_is_on_for_another(client, user, other_user):
    switch_off(user)

    assert page(client, other_user).status_code == 200


def test_off_the_companies_page_does_not_offer_the_map(client, user):
    client.force_login(user)
    url = reverse("jobs:company_map")
    assert url in client.get(reverse("jobs:company_list")).content.decode()

    switch_off(user)

    assert url not in client.get(reverse("jobs:company_list")).content.decode()


def test_off_an_address_says_nothing_of_the_map(client, user):
    company = Company.objects.create(owner=user, name="One", location="Lisbon")
    switch_off(user)
    client.force_login(user)

    html = client.get(reverse("jobs:company_update", args=[company.pk])).content.decode()

    assert "data-map-status-line" not in html
    address = address_of(company)
    assert client.get(reverse("jobs:address_place", args=[address.pk])).status_code == 404


def test_off_nothing_is_placed_and_nothing_stored_is_touched(client, user, monkeypatch, tmp_path):
    with_cities(monkeypatch, tmp_path, LISBON, BERLIN)
    company = Company.objects.create(owner=user, name="One", location="Lisbon")
    switch_off(user)

    address = address_of(company)
    address.municipality, address.country = "Berlin", "DE"
    address.save()

    address.refresh_from_db()
    assert (address.latitude, address.longitude) == (38.72509, -9.1498), "kept as it was"
    assert address.placed_from == "|lisbon|pt", "the words it was placed from, still"


def test_switching_back_on_places_again_on_the_next_save(client, user, monkeypatch, tmp_path):
    with_cities(monkeypatch, tmp_path, LISBON, BERLIN)
    company = Company.objects.create(owner=user, name="One", location="Lisbon")
    switch_off(user)
    address = address_of(company)
    address.municipality, address.country = "Berlin", "DE"
    address.save()
    switch_on(user)

    address.save()

    assert (address.latitude, address.longitude) == (52.52437, 13.41053)


def test_an_address_changed_while_off_is_drawn_where_its_words_say(
    client, user, monkeypatch, tmp_path
):
    """The stored point belongs to the old words; the page does not draw it there."""
    with_cities(monkeypatch, tmp_path, LISBON, BERLIN)
    company = Company.objects.create(owner=user, name="One", location="Lisbon")
    switch_off(user)
    address = address_of(company)
    address.municipality, address.country = "Berlin", "DE"
    address.save()
    switch_on(user)

    (entry,) = page(client, user).context["places"]

    assert entry["label"] == "Berlin, Germany"
    assert entry["lon"] == pytest.approx(13.41053)
    address.refresh_from_db()
    assert address.longitude == pytest.approx(-9.1498), "a page view writes nothing"


def test_a_point_placed_by_hand_is_drawn_and_survives_the_switch(
    client, user, monkeypatch, tmp_path
):
    with_cities(monkeypatch, tmp_path, LISBON, BERLIN)
    company = Company.objects.create(owner=user, name="One", location="Lisbon")
    place_by_hand(client, user, address_of(company), latitude="52.52437", longitude="13.41053")

    switch_off(user)
    switch_on(user)

    (entry,) = page(client, user).context["places"]
    assert entry["lon"] == pytest.approx(13.41053)


# ------------------------------------------------- a contact's and your own addresses (#702)


def an_address(owner, holder, **extra):
    from postulo.core.models import PostalAddress

    fields = {"municipality": "Lisbon", "country": "PT", **extra}
    return PostalAddress.objects.create(owner=owner, holder=holder, **fields)


def test_a_contacts_address_is_drawn_only_when_asked(client, user, monkeypatch, tmp_path):
    from postulo.jobs.models import Contact

    with_cities(monkeypatch, tmp_path, LISBON)
    company = Company.objects.create(owner=user, name="Aperture")
    someone = Contact.objects.create(owner=user, company=company, name="Caroline")
    address = an_address(user, someone, kind="home", street="Rua Secreta 1")

    assert page(client, user).context["places"] == []

    address.on_map = True
    address.save()
    html = page(client, user).content.decode()

    (entry,) = page(client, user).context["places"]
    assert entry["people"] == [
        ("Caroline", reverse("jobs:contact_update", args=[someone.pk]), "Home")
    ]
    assert "Rua Secreta" not in html, "never a street on the overview"


def test_your_own_address_is_never_drawn_unless_asked(client, user, monkeypatch, tmp_path):
    with_cities(monkeypatch, tmp_path, LISBON)
    address = an_address(user, user.profile, kind="home")

    assert page(client, user).context["places"] == []
    address.on_map = True
    address.save()
    (entry,) = page(client, user).context["places"]
    assert entry["people"][0][0] == "You"


def test_another_accounts_addresses_are_never_drawn(client, user, other_user):
    theirs = Company.objects.create(owner=other_user, name="Theirs", location="Lisbon")
    an_address(other_user, other_user.profile, on_map=True)

    assert page(client, user).context["places"] == []
    assert theirs.postal_addresses.count() == 1


def test_a_contacts_form_offers_the_map_choice(client, user):
    from postulo.jobs.models import Contact

    company = Company.objects.create(owner=user, name="Aperture")
    someone = Contact.objects.create(owner=user, company=company, name="Caroline")
    an_address(user, someone)
    client.force_login(user)

    contact_page = client.get(reverse("jobs:contact_update", args=[someone.pk])).content.decode()
    company_page = client.get(reverse("jobs:company_update", args=[company.pk])).content.decode()

    assert "on_map" in contact_page
    assert "on_map" not in company_page, "a company's addresses are always drawn"


def test_the_map_is_the_whole_window_with_its_heading_over_it(client, user):
    """#849: the heading reads Companies, a Table button goes back, and the page's own
    padding and footer give way to the map."""
    Company.objects.create(owner=user, name="One", location="Lisbon")

    html = page(client, user).content.decode()

    assert "data-map-stage" in html
    assert "<h1" in html and ">Companies<" in html.replace("\n", "").replace("  ", "")
    assert f'href="{reverse("jobs:company_list")}"' in html and "data-map-table" in html
    assert 'data-icon="table"' in html
    assert 'class="mx-auto max-w-none  outline-none"' in html, "no padding around the map"


def test_the_footers_links_are_drawn_once_over_the_map(client, user):
    Company.objects.create(owner=user, name="One", location="Lisbon")

    html = page(client, user).content.decode()

    assert html.count("data-source") == 1, "the footer's line is over the map, not below it"
    assert html.count("data-powered") == 1
    assert "map-footer" in html


def test_the_heading_has_no_card_and_no_count_and_the_table_is_its_own(client, user):
    """#909: the heading lies on the map with no box and no count of places under it, the
    way back to the table is at the top of the end side, and the list of places is under
    the heading, in one column down the start side."""
    Company.objects.create(owner=user, name="One", location="Lisbon")

    html = page(client, user).content.decode()

    heading = html[html.index("data-map-heading") - 200 : html.index("data-map-heading")]
    assert "map-overlay" not in heading, "no card around the heading"
    assert "data-map-count" not in html and "One place" not in html
    table = html[html.rindex("<a", 0, html.index("data-map-table")) : html.index("data-map-table")]
    assert "absolute end-3 top-3" in table
    column = html[html.index("data-map-column") : html.index("data-map-table")]
    assert column.index("data-map-heading") < column.index("data-map-drawer")


def test_the_footer_over_the_map_is_its_compact_line_in_the_end_corner(client, user):
    """#909: bottom right, never the motto, Source code and Help as icons named by their
    tooltips, then a dot and the map's credit."""
    Company.objects.create(owner=user, name="One", location="Lisbon")

    html = page(client, user).content.decode()

    footer = re.search(r"<footer[^>]*map-footer.*?</footer>", html, re.S)[0]
    assert "absolute end-3 bottom-3" in footer.split(">")[0]
    assert "data-motto" not in footer
    assert footer.index("data-powered") < footer.index("data-source") < footer.index("data-help")
    assert footer.index("data-help") < footer.index('data-icon="dot"') < footer.index(">Leaflet<")
    for tip, words in (("footer-tip-source", "Source code"), ("footer-tip-help", "Help")):
        assert f'aria-labelledby="{tip}"' in footer
        assert f'<span id="{tip}" data-tooltip>{words}</span>' in footer
    for mark in ("data-source", "data-help"):
        link = footer[footer.index(mark) : footer.index("</a>", footer.index(mark))]
        assert "<span" not in link, "the icon alone, no words beside it"


def test_the_map_buttons_ask_for_their_tooltips_beside_them(client, user):
    """#915: the three sentences ask for the inline-start side, which faces into the map."""
    Company.objects.create(owner=user, name="One", location="Lisbon")

    html = page(client, user).content.decode()

    for tip in ("map-tip-zoom-in", "map-tip-zoom-out", "map-tip-locate"):
        assert f'<span id="{tip}" data-tooltip data-tooltip-place="start">' in html


def test_the_table_link_loses_its_word_on_phones_but_keeps_it_and_has_a_tooltip(client, user):
    """#916: the word is visually hidden below `sm`, not removed; "Table view" is its
    tooltip, asked for below the link and for phones only."""
    Company.objects.create(owner=user, name="One", location="Lisbon")

    html = page(client, user).content.decode()

    mark = html.index("data-map-table")
    link = html[html.rindex("<a", 0, mark) : html.index("</a>", mark)]
    assert '<span class="sr-only sm:not-sr-only">Table</span>' in link
    assert 'aria-describedby="map-tip-table"' in link and "max-sm:size-9" in link
    assert (
        '<span id="map-tip-table" data-tooltip data-tooltip-place="below"'
        " data-tooltip-narrow hidden>Table view</span>"
    ) in html


def test_the_map_buttons_have_their_tooltips(client, user):
    Company.objects.create(owner=user, name="One", location="Lisbon")

    html = page(client, user).content.decode()

    for tip, words in (("map-tip-zoom-in", "Zoom in"), ("map-tip-zoom-out", "Zoom out")):
        assert f'<span id="{tip}" data-tooltip data-tooltip-place="start">{words}</span>' in html


def test_every_other_page_keeps_the_motto_and_the_words(client, user):
    client.force_login(user)

    html = client.get(reverse("jobs:company_list")).content.decode()

    assert "data-motto" in html and "footer-tip-source" not in html
    assert 'title="Help"' in html


def test_the_places_are_a_list_that_opens_without_a_script(client, user):
    Company.objects.create(owner=user, name="One", location="Lisbon")

    html = page(client, user).content.decode()

    assert "<details" in html and "data-map-drawer" in html
    drawer = html[html.index("<details") : html.index(">", html.index("<details"))]
    assert " open" in drawer


def test_the_table_offers_the_map_with_its_icon(client, user):
    client.force_login(user)

    html = client.get(reverse("jobs:company_list")).content.decode()

    switch = html[html.index("data-map-switch") - 200 : html.index("data-map-switch") + 300]
    assert 'data-icon="map"' in switch


def test_every_other_page_keeps_its_footer_and_padding(client, user):
    client.force_login(user)

    html = client.get(reverse("jobs:company_list")).content.decode()

    assert html.count("data-source") == 1
    assert "px-4 py-8" in html


def test_only_the_map_may_ask_where_the_browser_is(client, user):
    """#850: Near me asks on the map, and nothing else may raise the prompt."""
    Company.objects.create(owner=user, name="One", location="Lisbon")

    on_the_map = page(client, user)
    elsewhere = client.get(reverse("jobs:company_list"))

    assert on_the_map.headers["Permissions-Policy"] == "geolocation=(self)"
    assert elsewhere.headers["Permissions-Policy"] == "geolocation=()"


def test_near_me_is_offered_in_the_readers_words(client, user):
    Company.objects.create(owner=user, name="One", location="Lisbon")

    html = page(client, user).content.decode()

    assert (
        '<span id="map-tip-locate" data-tooltip data-tooltip-place="start">Near me</span>' in html
    )
    assert "data-map-locate-refused=" in html and "data-map-locate-failed=" in html
    assert 'role="status" data-map-status' in html
    assert 'data-icon="locate-fixed"' in html


# ------------------------------------------------------- several addresses, several pins (#703)


def test_a_company_with_two_towns_is_at_both(client, user, monkeypatch, tmp_path):
    from postulo.core.models import PostalAddress

    with_cities(monkeypatch, tmp_path, LISBON, BERLIN)
    company = Company.objects.create(owner=user, name="Aperture", location="Lisbon")
    PostalAddress.objects.create(
        owner=user, holder=company, kind="work", municipality="Berlin", country="DE"
    )

    context = page(client, user).context

    counts = {entry["label"]: entry["count"] for entry in context["places"]}
    assert counts == {"Lisbon, Portugal": 1, "Berlin, Germany": 1}
    assert len(context["drawn"]) == 2
    assert {entry["company"] for entry in context["drawn"]} == {context["drawn"][0]["company"]}
    assert context["counted_at_several"]
    assert "counted at each" in page(client, user).content.decode()


def test_two_spellings_of_one_town_are_one_place(client, user, monkeypatch, tmp_path):
    with_cities(monkeypatch, tmp_path, ("Lisbon", "38.72509", "-9.1498", "PT", 517802))
    from postulo.core.models import PostalAddress

    one = Company.objects.create(owner=user, name="One")
    two = Company.objects.create(owner=user, name="Two")
    PostalAddress.objects.create(owner=user, holder=one, municipality="Lisbon", country="PT")
    PostalAddress.objects.create(owner=user, holder=two, municipality="LISBON", country="PT")

    (place,) = page(client, user).context["places"]

    assert place["count"] == 2


def test_two_offices_of_one_company_in_one_town_count_it_once(client, user, monkeypatch, tmp_path):
    from postulo.core.models import PostalAddress

    with_cities(monkeypatch, tmp_path, LISBON)
    company = Company.objects.create(owner=user, name="Aperture", location="Lisbon")
    PostalAddress.objects.create(
        owner=user,
        holder=company,
        kind="postal",
        street="Apartado 1",
        municipality="Lisbon",
        country="PT",
    )

    context = page(client, user).context

    (place,) = context["places"]
    assert place["count"] == 1
    assert [row["primary"] for row in place["companies"]] == [True, False]
    assert not context["counted_at_several"], "said only when it is true"


def test_the_clusters_are_counted_in_the_readers_words(client, user):
    Company.objects.create(owner=user, name="One", location="Lisbon")

    html = page(client, user).content.decode()

    assert 'data-map-cluster="Companies here: {count}"' in html
    assert "leaflet.markercluster.js" in html and "MarkerCluster.css" in html


def test_a_pin_carries_the_holders_picture_where_there_is_one(client, user, monkeypatch, tmp_path):
    """A company's logo, the person's own picture, and a contact never (#914)."""
    from postulo.jobs.models import Contact

    with_cities(monkeypatch, tmp_path, LISBON)
    with_logo = Company.objects.create(owner=user, name="Aperture", has_logo=True)
    without = Company.objects.create(owner=user, name="Black Mesa")
    someone = Contact.objects.create(owner=user, company=without, name="Caroline")
    for holder in (with_logo, without):
        an_address(user, holder)
    an_address(user, someone, on_map=True)
    an_address(user, user.profile, on_map=True)
    user.profile.has_avatar = True
    user.profile.save()

    pictures = {entry["name"]: entry["picture"] for entry in page(client, user).context["drawn"]}

    logo = reverse("jobs:company_logo", args=[with_logo.pk])
    assert pictures["Aperture"].startswith(f"{logo}?v=")
    assert pictures["You"].startswith(f"{reverse('accounts:avatar', args=[user.pk])}?v=")
    assert pictures["Black Mesa"] == "" and pictures["Caroline"] == ""


def test_a_picture_pin_falls_back_to_the_dot_and_keeps_its_name():
    """Built only when drawn, decorative inside a marker that is named, and a dot on error."""
    from pathlib import Path

    script = (Path(__file__).resolve().parents[1] / "src/postulo/static/js/app.js").read_text(
        encoding="utf-8"
    )
    drawing = script[script.index("var PictureIcon") : script.index("layer.addTo(map)")]

    assert 'image.alt = ""' in drawing and "alt: entry.name" in drawing
    assert '"error"' in drawing and "marker.setIcon" in drawing
    assert "innerHTML" not in drawing and "html: '" not in drawing, "the address is not markup"
