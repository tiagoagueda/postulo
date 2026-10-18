"""The companies' places as GeoJSON, out and in (#880)."""

from __future__ import annotations

import json

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from postulo.core.models import PostalAddress
from postulo.jobs import geojson, placing
from postulo.jobs.models import Company, CompanyIdentifier, Contact


def placed_company(user, name="Aperture", lat=38.72, lon=-9.14):
    company = Company.objects.create(owner=user, name=name)
    address = PostalAddress.objects.create(
        owner=user, holder=company, kind="work", municipality="Lisbon", country="PT"
    )
    placing.place_by_hand(address, lat, lon)
    return company


def a_file(*features) -> SimpleUploadedFile:
    body = json.dumps({"type": "FeatureCollection", "features": list(features)}).encode()
    return SimpleUploadedFile("places.geojson", body, content_type="application/geo+json")


def a_point(lon, lat, **properties):
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
        "properties": properties,
    }


def test_the_export_is_a_feature_collection_longitude_first(client, user):
    placed_company(user)
    client.force_login(user)

    response = client.get(reverse("jobs:company_places"), {"download": "1"})

    assert response["Content-Type"].startswith("application/geo+json")
    document = json.loads(response.content)
    (feature,) = document["features"]
    assert document["type"] == "FeatureCollection"
    assert feature["geometry"] == {"type": "Point", "coordinates": [-9.14, 38.72]}
    assert feature["properties"]["name"] == "Aperture"
    assert feature["properties"]["placed_by"] == "manual", "how precise it is, said"


def test_the_export_never_holds_a_persons_home_or_another_account(client, user, other_user):
    placed_company(user)
    placed_company(other_user, name="Theirs")
    someone = Contact.objects.create(owner=user, name="Caroline")
    home = PostalAddress.objects.create(
        owner=user, holder=someone, street="Rua Secreta 1", municipality="Lisbon", on_map=True
    )
    placing.place_by_hand(home, 1.0, 2.0)
    own = PostalAddress.objects.create(owner=user, holder=user.profile, on_map=True)
    placing.place_by_hand(own, 3.0, 4.0)

    names = [f["properties"]["name"] for f in geojson.export(user)["features"]]

    assert names == ["Aperture"]


def test_points_are_matched_and_shown_before_anything_is_placed(client, user):
    company = placed_company(user, lat=1.0, lon=1.0)
    client.force_login(user)

    client.post(
        reverse("jobs:company_places"),
        {"file": a_file(a_point(-9.1, 38.7, name="aperture"), a_point(0, 0, name="Nobody"))},
    )
    page = client.get(reverse("jobs:company_places")).content.decode()

    assert "None of your companies has this name" in page
    assert 'name="chosen"' in page and "checked" not in page.split("data-geojson-review")[1]
    assert company.postal_addresses.get().latitude == 1.0, "nothing placed yet"


def test_a_ticked_point_places_the_company_by_hand(client, user):
    company = placed_company(user, lat=1.0, lon=1.0)
    client.force_login(user)
    client.post(
        reverse("jobs:company_places"), {"file": a_file(a_point(-9.1, 38.7, name="Aperture"))}
    )

    client.post(reverse("jobs:company_places"), {"action": "confirm", "chosen": ["0"]})

    address = company.postal_addresses.get()
    assert (address.latitude, address.longitude, address.placed_by) == (38.7, -9.1, "manual")


def test_a_wikidata_identifier_matches_whatever_the_name(client, user):
    company = placed_company(user, lat=1.0, lon=1.0)
    CompanyIdentifier.objects.create(owner=user, company=company, scheme="wikidata", value="Q95")

    (entry,) = geojson.review(
        user,
        geojson.read(
            json.dumps(
                {
                    "type": "FeatureCollection",
                    "features": [a_point(2, 3, name="Other", wikidata="q95")],
                }
            ).encode()
        ),
    )

    assert entry["company"] == company and entry["outcome"] == "place"


def test_a_company_with_no_address_is_listed_and_not_placed(user):
    Company.objects.create(owner=user, name="Aperture")
    points = geojson.read(
        json.dumps(
            {"type": "FeatureCollection", "features": [a_point(2, 3, name="Aperture")]}
        ).encode()
    )

    (entry,) = geojson.review(user, points)

    assert entry["outcome"] == "no_address"
    assert geojson.apply(user, points, {0}) == 0


def test_another_accounts_company_is_never_matched(user, other_user):
    placed_company(other_user, name="Theirs")
    points = geojson.read(
        json.dumps(
            {"type": "FeatureCollection", "features": [a_point(2, 3, name="Theirs")]}
        ).encode()
    )

    (entry,) = geojson.review(user, points)

    assert entry["outcome"] == "unmatched"


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b'{"type": "Feature"}',
        b'{"type": "FeatureCollection", "features": "x"}',
    ],
)
def test_a_file_that_is_not_a_collection_is_refused(body):
    with pytest.raises(geojson.GeoJsonRefused):
        geojson.read(body)


def test_a_deeply_nested_document_is_refused_not_a_server_error(client, user):
    """A JSON array nested thousands deep ends in RecursionError, not a ValueError (#959)."""
    nested = b"[" * 200_000 + b"]" * 200_000
    with pytest.raises(geojson.GeoJsonRefused, match="not GeoJSON"):
        geojson.read(nested)
    client.force_login(user)
    body = SimpleUploadedFile("places.geojson", nested, content_type="application/geo+json")
    response = client.post(reverse("jobs:company_places"), {"file": body}, follow=True)
    assert response.status_code == 200
    assert "That file is not GeoJSON." in response.content.decode()


def test_points_off_the_globe_or_without_a_name_are_left_out():
    points = geojson.read(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    a_point(200, 0, name="Off"),
                    a_point(0, 0),
                    {"type": "Feature", "geometry": {"type": "LineString"}, "properties": {}},
                    a_point(1, 2, name="Kept"),
                ],
            }
        ).encode()
    )

    assert [point.name for point in points] == ["Kept"]


def test_a_file_too_large_is_refused():
    with pytest.raises(geojson.GeoJsonRefused, match="larger"):
        geojson.read(b" " * (geojson.MAX_BYTES + 1))


def test_without_the_map_the_format_is_off(client, user):
    from postulo.plugins.maps import MAPS
    from postulo.plugins.models import PluginPolicy
    from postulo.plugins.policy import forget_decisions

    PluginPolicy.objects.create(plugin=MAPS, person=user, state=PluginPolicy.State.FORCED_OFF)
    forget_decisions()
    client.force_login(user)

    assert client.get(reverse("jobs:company_places")).status_code == 404
    page = client.get(reverse("jobs:company_list")).content.decode()
    assert "data-places-geojson" not in page


def test_the_companies_page_offers_it(client, user):
    client.force_login(user)

    page = client.get(reverse("jobs:company_list")).content.decode()

    assert reverse("jobs:company_places") in page
