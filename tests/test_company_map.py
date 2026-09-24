"""The companies' map: the list is the page, and the map draws the same list (#108).

A map is the classic screen-reader failure, so these tests hold the two promises the
screen makes: everything the drawing says is text on the page, and the drawing is
something the server writes rather than a script that fetches tiles.
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
    """The dot is where the data puts it, in the outline's own projection (#108)."""
    (tmp_path / places.CITIES_FILE).write_text(city_table(LISBON), encoding="utf-8")
    monkeypatch.setattr(places, "DATA_DIR", tmp_path)
    Company.objects.create(owner=user, name="One", location="Lisbon")

    html = page(client, user).content.decode()

    # cx = lon + 180, cy = 90 - lat: the projection the outline is drawn in.
    assert 'cx="170.9"' in html
    assert 'cy="51.3"' in html


def test_a_place_without_coordinates_is_listed_and_not_drawn(client, user, tmp_path, monkeypatch):
    monkeypatch.setattr(places, "DATA_DIR", tmp_path)
    Company.objects.create(owner=user, name="One", location="Remote")

    html = page(client, user).content.decode()

    assert "Remote" in html
    assert "Not on the map" in html


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
    assert by_label["Berlin"]["radius"] > by_label["Lisbon"]["radius"]


def test_the_map_is_the_servers_own_drawing(client, user):
    """The drawing is an SVG the server writes: the outline is in the page, and no
    tile server or map library is asked for (#108)."""
    Company.objects.create(owner=user, name="One", location="Lisbon")

    html = page(client, user).content.decode().lower()

    assert "m358.1 107.5" in html, "the server's own outline is in the page"
    assert "openstreetmap" not in html
    assert "leaflet" not in html


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
