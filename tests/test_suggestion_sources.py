"""Where a field's suggestions come from: the `suggestion-source` kind (#1252).

NACE is the first vocabulary behind it. The datalist an industry input offers is what the
registry's enabled sources answer with, and a plainer field when none is.
"""

from __future__ import annotations

import pytest

from postulo.core import suggestion_sources
from postulo.core.memo import forget_decisions
from postulo.jobs import industries
from postulo.jobs.forms import CompanyForm
from postulo.plugins import api, base, registry
from postulo.plugins.models import PluginPolicy


@pytest.fixture(autouse=True)
def _fresh_registry():
    """A test that adds a plugin must not leave the registry's cache holding it."""
    registry._cache.pop("suggestion-source", None)
    yield
    registry._cache.pop("suggestion-source", None)


def _offer_cities(field, exclude=(), language="", first=()):
    return [("Lisbon", "PT"), ("Porto", "PT")] if field == "city" else []


def _source(**changes):
    values = dict(  # noqa: C408
        key="cities",
        label="Cities",
        fields=("city",),
        languages=(),
        offline=True,
        licence="CC0",
        offer=_offer_cities,
    )
    values.update(changes)
    return api.SuggestionSource(**values)


def test_the_surface_holds_what_a_suggestion_source_needs():
    assert "SuggestionSource" in api.__all__ and "SuggestionSourcePlugin" in api.__all__
    assert api.SuggestionSource is suggestion_sources.Source
    assert api.SuggestionSourcePlugin is base.SuggestionSourcePlugin


def test_nace_is_a_shipped_suggestion_source_that_is_offline_and_licensed():
    plugin = registry.find_plugin("suggestion-source", "nace")
    (source,) = plugin.sources

    assert plugin.name == "nace" and plugin.kind == "suggestion-source"
    assert source.offline and not source.hosts
    assert source.licence and source.fields == ("industry",)
    assert plugin in registry.plugins("suggestion-source")


def test_the_registry_offers_what_the_industry_module_offered(db):
    expected = industries.offered(exclude=["software"], first=["62"])

    got = suggestion_sources.offered("industry", exclude=["software"], first=["62"])

    assert got == expected
    assert got[0][0].casefold() != "software"


def test_a_field_no_source_knows_is_a_plain_input(db):
    assert suggestion_sources.offered("school") == []


def test_a_plugin_adds_a_source_and_its_suggestions_merge_after_the_shipped_ones(db, monkeypatch):
    class Cities:
        name = "cities"
        kind = "suggestion-source"
        sources = (
            _source(),
            _source(
                key="also",
                fields=("industry",),
                offer=lambda *a, **k: [("Software", "X"), ("Brewing", "")],
            ),
        )

    monkeypatch.setitem(
        registry._builtin, "suggestion-source", [*registry._builtin["suggestion-source"], Cities]
    )

    assert suggestion_sources.offered("city") == [("Lisbon", "PT"), ("Porto", "PT")]
    industry = [text for text, _ in suggestion_sources.offered("industry")]
    assert industry.count("Software") == 1, "one entry per text, the shipped source's kept"
    assert "Brewing" in industry


@pytest.mark.parametrize(
    "bad",
    [
        {"offline": False},  # dials out and names no host
        {"hosts": ("example.org",)},  # offline and names a host
        {"licence": ""},
        {"fields": ()},
        {"key": "x" * 41},
        {"offer": None},
    ],
)
def test_a_source_that_cannot_be_used_is_left_out(bad):
    assert suggestion_sources._usable(_source(**bad)) is False
    assert suggestion_sources._usable(_source()) is True
    assert suggestion_sources._usable(_source(offline=False, hosts=("api.example.org",)))
    assert suggestion_sources._usable("not a source") is False


def test_a_network_source_is_never_asked_by_a_page(db, monkeypatch):
    asked = []

    def offer(field, **_kw):
        asked.append(field)
        return [("Remote", "")]

    class Remote:
        name = "remote"
        kind = "suggestion-source"
        sources = (_source(key="remote", offline=False, hosts=("api.example.org",), offer=offer),)

    monkeypatch.setitem(registry._builtin, "suggestion-source", [Remote])

    assert suggestion_sources.sources("city")
    assert suggestion_sources.offered("city") == [] and asked == []


def test_a_source_that_raises_makes_a_plainer_field_and_not_a_broken_one(db, monkeypatch):
    def broken(*_a, **_k):
        raise RuntimeError("boom")

    class Broken:
        name = "broken"
        kind = "suggestion-source"
        sources = (_source(key="broken", fields=("industry",), offer=broken),)

    monkeypatch.setitem(
        registry._builtin, "suggestion-source", [*registry._builtin["suggestion-source"], Broken]
    )

    assert suggestion_sources.offered("industry") == industries.offered()


def test_the_company_form_offers_what_the_source_offers(user):
    assert CompanyForm(user=user).suggestions == industries.offered(exclude=[], first=[])


def test_with_nace_forced_off_the_form_still_works_with_a_plain_input(user):
    PluginPolicy.objects.create(plugin="nace", person=user, state=PluginPolicy.State.FORCED_OFF)
    forget_decisions()

    form = CompanyForm(user=user)

    assert form.suggestions == []
    assert "industries" in form.fields, "the field is still there to type in"


def test_nace_shows_in_the_plugins_list_as_a_suggestion_source():
    from postulo.plugins import kinds, policy

    assert "suggestion-source" in policy.GOVERNED_KINDS
    assert str(kinds.label_for("suggestion-source")) == "Suggestions"
