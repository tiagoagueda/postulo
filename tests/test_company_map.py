"""The companies' map: the list is the page, and the map draws the same list (#108, #898).

A map is the classic screen-reader failure, so these tests hold the promise the screen
makes: everything the drawing says is text on the page, and what Leaflet draws is handed to
it as data. Where its tiles come from, and what the page's policy then allows, is held in
`tests/test_map_tiles.py`.
"""

from __future__ import annotations

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
    """The question the map answers is where the search is spread: the size of a dot
    is the number of companies at the place, so a place of five is bigger than a
    place of one (#108)."""
    (tmp_path / places.CITIES_FILE).write_text(city_table(LISBON, BERLIN), encoding="utf-8")
    monkeypatch.setattr(places, "DATA_DIR", tmp_path)
    Company.objects.create(owner=user, name="A", location="Lisbon")
    for number in range(2, 7):
        Company.objects.create(owner=user, name=f"B{number}", location="Berlin")

    context = page(client, user).context

    by_label = {entry["label"]: entry for entry in context["places"]}
    assert by_label["Lisbon"]["count"] == 1
    assert by_label["Berlin"]["count"] == 5
    drawn = {entry["label"]: entry["count"] for entry in context["drawn"]}
    assert drawn == {"Berlin": 5, "Lisbon": 1}, "the script sizes each dot by its count"


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


def edit(client, user, company, **fields):
    client.force_login(user)
    fields = {"name": company.name, "location": company.location, **fields}
    return client.post(reverse("jobs:company_update", args=[company.pk]), fields)


def test_a_wrong_guess_is_corrected_on_the_companys_form(client, user, monkeypatch, tmp_path):
    """Somewhere to correct a wrong guess by hand: the company's own form, where the
    correction is a place name and the record keeps it as a person's, not a guess (#108).
    """
    (tmp_path / places.CITIES_FILE).write_text(city_table(LISBON, BERLIN), encoding="utf-8")
    monkeypatch.setattr(places, "DATA_DIR", tmp_path)
    company = Company.objects.create(owner=user, name="One", location="Lisbon")

    response = edit(client, user, company, location_correction="Berlin")

    assert response.status_code == 302
    company.refresh_from_db()
    assert company.location_resolved_by == "manual"
    assert company.location_resolved_from == "Berlin"
    assert (company.location_lat, company.location_lon) == (52.52437, 13.41053)

    # The correction is a fact a person made, and the next save of the same location
    # does not guess it away.
    company.save()
    company.refresh_from_db()
    assert company.location_resolved_by == "manual"
    assert company.location_resolved_from == "Berlin"


def test_a_correction_the_table_cannot_place_is_refused(client, user, monkeypatch, tmp_path):
    """A correction the table cannot place is refused in the form, and the guess stands
    where it was: a correction that would leave the pin nowhere is not saved as a hole."""
    (tmp_path / places.CITIES_FILE).write_text(city_table(LISBON), encoding="utf-8")
    monkeypatch.setattr(places, "DATA_DIR", tmp_path)
    company = Company.objects.create(owner=user, name="One", location="Lisbon")

    response = edit(client, user, company, location_correction="Nowhere")

    assert response.status_code == 200, "the form comes back with the reason"
    company.refresh_from_db()
    assert company.location_resolved_by == "geonames"
    assert company.location_resolved_from == "Lisbon"


def test_taking_a_correction_off_guesses_again(client, user, monkeypatch, tmp_path):
    """The form shows a correction where it is, and blank again means the location text
    guesses (#108)."""
    (tmp_path / places.CITIES_FILE).write_text(city_table(LISBON, BERLIN), encoding="utf-8")
    monkeypatch.setattr(places, "DATA_DIR", tmp_path)
    company = Company.objects.create(owner=user, name="One", location="Lisbon")

    edit(client, user, company, location_correction="Berlin")
    company.refresh_from_db()
    assert company.location_resolved_by == "manual"

    client.force_login(user)
    html = client.get(reverse("jobs:company_update", args=[company.pk])).content.decode()
    assert "Or, exactly where" in html
    assert "Berlin" in html, "the correction a person made is shown where it is"

    response = edit(client, user, company, location_correction="")

    assert response.status_code == 302
    company.refresh_from_db()
    assert company.location_resolved_by == "geonames"
    assert (company.location_lat, company.location_lon) == (38.72509, -9.1498)


# The switch (#700): one feature decides whether the map is offered, and off deletes nothing.


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


def test_off_the_form_does_not_offer_the_correction(client, user):
    company = Company.objects.create(owner=user, name="One", location="Lisbon")
    client.force_login(user)
    url = reverse("jobs:company_update", args=[company.pk])
    assert "Or, exactly where" in client.get(url).content.decode()

    switch_off(user)
    html = client.get(url).content.decode()

    assert "Or, exactly where" not in html
    assert "offline table of cities" not in html


def test_off_nothing_is_placed_and_nothing_stored_is_touched(client, user, monkeypatch, tmp_path):
    with_cities(monkeypatch, tmp_path, LISBON, BERLIN)
    company = Company.objects.create(owner=user, name="One", location="Lisbon")
    assert company.location_resolved_by == "geonames"
    stored = (company.location_lat, company.location_lon, company.location_resolved_at)

    switch_off(user)
    edit(client, user, company, location="Berlin")

    company.refresh_from_db()
    assert company.location == "Berlin"
    assert (company.location_lat, company.location_lon) == stored[:2], "the old place is kept"
    assert company.location_resolved_from == "Lisbon", "the text it belongs to is kept too"
    assert company.location_resolved_at == stored[2]

    new = Company.objects.create(owner=user, name="Two", location="Berlin")
    assert new.location_lat is None and new.location_resolved_by == ""


def test_switching_back_on_guesses_again_on_the_next_save(client, user, monkeypatch, tmp_path):
    with_cities(monkeypatch, tmp_path, LISBON, BERLIN)
    company = Company.objects.create(owner=user, name="One", location="Lisbon")
    switch_off(user)
    edit(client, user, company, location="Berlin")

    switch_on(user)
    company.refresh_from_db()
    company.save()

    company.refresh_from_db()
    assert (company.location_lat, company.location_lon) == (52.52437, 13.41053)
    assert company.location_resolved_from == "Berlin"


def test_a_company_edited_while_off_is_drawn_where_its_text_says(
    client, user, monkeypatch, tmp_path
):
    """The stored coordinate belongs to the old text; the page does not draw it there."""
    with_cities(monkeypatch, tmp_path, LISBON, BERLIN)
    company = Company.objects.create(owner=user, name="One", location="Lisbon")
    switch_off(user)
    edit(client, user, company, location="Berlin")
    switch_on(user)

    response = page(client, user)

    (entry,) = response.context["places"]
    assert entry["label"] == "Berlin"
    assert entry["lon"] == pytest.approx(13.41053)
    company.refresh_from_db()
    assert company.location_resolved_from == "Lisbon", "a page view writes nothing"


def test_a_correction_a_person_made_is_drawn_and_survives_the_switch(
    client, user, monkeypatch, tmp_path
):
    with_cities(monkeypatch, tmp_path, LISBON, BERLIN)
    company = Company.objects.create(owner=user, name="One", location="Lisbon")
    edit(client, user, company, location_correction="Berlin")

    switch_off(user)
    switch_on(user)

    (entry,) = page(client, user).context["places"]
    assert entry["lon"] == pytest.approx(13.41053)


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

    assert 'data-map-locate="Near me"' in html
    assert "data-map-locate-refused=" in html and "data-map-locate-failed=" in html
    assert 'role="status" data-map-status' in html
    assert 'data-icon="locate-fixed"' in html
