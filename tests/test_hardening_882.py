"""The map module is Maps, credits what it uses, and keeps its name (#882)."""

from __future__ import annotations

from pathlib import Path

import pytest
from django.urls import reverse

from postulo.plugins.api import description_of, label_of
from postulo.plugins.maps import MAPS, MapsFeature

ICONS = Path(__file__).resolve().parents[1] / "src" / "postulo" / "static" / "icons"


def test_the_label_is_maps_and_the_name_policy_rows_use_stays():
    assert MAPS == "maps"
    assert str(label_of(MapsFeature)) == "Maps"


def test_the_description_credits_the_data_it_uses():
    text = str(description_of(MapsFeature))

    for credit in ("GeoNames", "CC BY 3.0", "Natural Earth", "CLDR"):
        assert credit in text, credit


@pytest.mark.django_db
def test_the_map_page_credits_geonames_with_or_without_a_dot_to_show(client, user):
    client.force_login(user)

    page = client.get(reverse("jobs:company_map")).content.decode()

    assert "<title>" in page and "Maps" in page.split("<title>")[1].split("</title>")[0]
    credits = page[page.index("data-map-credits") :].split("</p>")[0]
    assert "GeoNames" in credits and "CC BY 3.0" in credits and "Natural Earth" in credits
    assert 'rel="noopener noreferrer external"' in credits


def test_the_map_icon_is_shipped_for_its_logo():
    assert (ICONS / "map.svg").is_file()
