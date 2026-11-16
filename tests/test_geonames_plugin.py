"""GeoNames as a bundled suggestion source: the city box, and the table's other readers (#1252).

The test settings run without the GeoNames data (as CI does), so a table is built into a
temporary directory where one is needed.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from postulo.core import suggest, suggestion_sources
from postulo.core.memo import forget_decisions
from postulo.core.postal import PostalAddressForm
from postulo.jobs import places, placing
from postulo.plugins import registry
from postulo.plugins.models import PluginPolicy

ROWS = [
    # id, name, ascii, alternates, lat, lon, class, code, country, ..., population (col 14)
    (
        "1",
        "Lisbon",
        "Lisbon",
        "Lisboa,Lissabon",
        "38.72",
        "-9.14",
        "P",
        "PPLC",
        "PT",
        "",
        "",
        "",
        "",
        "",
        "545245",
    ),
    ("2", "Lisieux", "Lisieux", "", "49.14", "0.23", "P", "PPL", "FR", "", "", "", "", "", "22000"),
    (
        "3",
        "Liskeard",
        "Liskeard",
        "",
        "50.45",
        "-4.46",
        "P",
        "PPL",
        "GB",
        "",
        "",
        "",
        "",
        "",
        "9000",
    ),
    (
        "4",
        "Porto",
        "Porto",
        "Oporto",
        "41.15",
        "-8.61",
        "P",
        "PPLA",
        "PT",
        "",
        "",
        "",
        "",
        "",
        "237000",
    ),
]


@pytest.fixture
def table(tmp_path, monkeypatch):
    """A tiny lookup in place of the GeoNames download, which the tests never have."""
    source = tmp_path / places.CITIES_FILE
    source.write_text("\n".join("\t".join(row) for row in ROWS) + "\n", encoding="utf-8")
    monkeypatch.setattr(places, "DATA_DIR", tmp_path)
    places._index.cache_clear()
    yield tmp_path
    places._index.cache_clear()


@pytest.fixture(autouse=True)
def _fresh_registry():
    registry._cache.pop("suggestion-source", None)
    forget_decisions()
    yield
    registry._cache.pop("suggestion-source", None)
    forget_decisions()


def _switch_off(user):
    PluginPolicy.objects.create(plugin="geonames", person=user, state=PluginPolicy.State.FORCED_OFF)
    forget_decisions()


def test_geonames_is_a_shipped_offline_licensed_source_of_cities():
    (plugin,) = [p for p in registry.plugins("suggestion-source") if p.name == "geonames"]
    (source,) = plugin.sources

    assert plugin.kind == "suggestion-source"
    assert source.fields == ("city",) and source.offline and not source.hosts
    assert source.licence


def test_a_city_is_suggested_from_what_was_typed_largest_first(db, table):
    found = suggestion_sources.offered("city", typed="lis")

    assert [text for text, _ in found] == ["Lisbon", "Lisieux", "Liskeard"]


def test_a_city_is_found_by_any_name_it_answers_to_and_shown_by_its_own(db, table):
    assert [t for t, _ in suggestion_sources.offered("city", typed="oport")] == ["Porto"]
    assert [t for t, _ in suggestion_sources.offered("city", typed="lissab")] == ["Lisbon"]


def test_nothing_is_suggested_for_one_letter_or_no_text_or_another_field(db, table):
    assert suggestion_sources.offered("city", typed="l") == []
    assert suggestion_sources.offered("city") == []
    (source,) = [s for s in suggestion_sources.sources("city") if s.key == "geonames"]
    assert source.offer("industry", typed="lis") == []


def test_without_the_table_the_box_is_plain(db):
    # The test settings have no GeoNames data, as CI has none.
    assert suggestion_sources.offered("city", typed="lisbon") == []


def test_the_endpoint_offers_the_person_s_own_towns_then_the_table_s(client, user, table):
    from postulo.core.models import PostalAddress

    PostalAddress.objects.create(owner=user, holder=user, municipality="Lisse", country="NL")
    client.force_login(user)

    html = client.get(reverse("core:suggest", args=["city"]), {"q": "lis"}).content.decode()

    assert html.index("Lisse") < html.index("Lisbon")
    assert "Lisieux" in html


def test_with_the_plugin_off_the_box_suggests_only_what_the_person_typed(client, user, table):
    from postulo.core.models import PostalAddress

    PostalAddress.objects.create(owner=user, holder=user, municipality="Lisse", country="NL")
    _switch_off(user)
    client.force_login(user)

    html = client.get(reverse("core:suggest", args=["city"]), {"q": "lis"}).content.decode()

    assert "Lisse" in html and "Lisbon" not in html


def test_the_town_box_of_an_address_is_a_suggestion_box():
    form = PostalAddressForm()

    assert isinstance(form.fields["municipality"].widget, suggest.SuggestInput)
    assert form.fields["municipality"].widget.attrs["data-suggest"].endswith("/municipality/")


def test_the_address_form_works_with_the_plugin_off(user, table):
    _switch_off(user)
    form = PostalAddressForm(
        data={"kind": "work", "municipality": "Lisbon", "country": "PT"}, person=user
    )

    assert form.is_valid(), form.errors


def _address(user, **parts):
    from postulo.core.models import PostalAddress

    return PostalAddress(owner=user, holder=user, kind="work", **parts)


def test_an_address_is_placed_from_its_town_while_the_plugin_is_on(user, table):
    address = _address(user, municipality="Lisbon", country="PT")

    placing.place(address)

    assert address.placed_by == "town" and address.latitude == pytest.approx(38.72)


def test_with_the_plugin_off_nothing_is_placed_and_nothing_stored_is_touched(user, table):
    address = _address(user, municipality="Lisbon", country="PT")
    address.latitude, address.longitude, address.placed_by = 1.0, 2.0, "town"
    address.placed_from = "an earlier reading"
    _switch_off(user)

    placing.place(address)

    assert (address.latitude, address.longitude, address.placed_by) == (1.0, 2.0, "town")
    assert address.placed_from == "an earlier reading"
