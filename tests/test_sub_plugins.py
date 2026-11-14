"""A plugin that offers many things, each switched by itself (postulo-apprise#20).

The registry reads a plugin's ``sub_plugins`` when it loads, the policy answers for each as for
any sub-module, and the administrator's plugins page lists and switches them under the plugin.
"""

from __future__ import annotations

import logging

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from postulo.plugins import api, policy, registry
from postulo.plugins.models import PluginPolicy

pytestmark = pytest.mark.django_db

User = get_user_model()
PASSWORD = "a-fairly-long-password-42"


class Chorus:
    """An installed plugin that offers four things: two on, one off to begin with, one that
    cannot work here."""

    name = "chorus"
    label = "Chorus"
    version = "1.0"
    kind = "source"

    def can_handle(self, url: str) -> bool:
        return False

    def parse(self, url: str, html: str):
        return None

    def sub_plugins(self):
        return [
            api.SubPlugin("alto", "Alto", "The middle voice"),
            api.SubPlugin("bass", "Bass", default_on=False),
            api.SubPlugin("tenor", "Tenor"),
            api.SubPlugin("piccolo", "Piccolo", unavailable="Needs the piccolo library."),
        ]


@pytest.fixture
def chorus(monkeypatch):
    monkeypatch.setattr(
        registry,
        "_load_third_party",
        lambda kind: [Chorus()] if kind == "source" else [],
    )
    registry.plugins("source", refresh=True)
    yield Chorus.name
    registry._cache.pop("source", None)


@pytest.fixture
def admin(db):
    return User.objects.create_user(
        email="admin@example.org", password=PASSWORD, username="admin-one", is_staff=True
    )


def test_the_registry_lists_what_a_plugin_declares_computed_at_load(chorus):
    entries = registry.sub_plugins(chorus)
    assert [entry.name for entry in entries] == [
        "chorus:alto",
        "chorus:bass",
        "chorus:tenor",
        "chorus:piccolo",
    ]
    assert {entry.parent for entry in entries} == {"chorus"}
    assert registry.find_sub_plugin("chorus:bass").sub.default_on is False
    assert registry.find_sub_plugin("chorus:nobody") is None
    assert registry.sub_plugins("someone-else") == []


def test_a_plugin_that_is_not_loaded_offers_nothing(chorus, monkeypatch):
    monkeypatch.setattr(registry, "_load_third_party", lambda kind: [])
    registry.plugins("source", refresh=True)
    assert registry.sub_plugins("chorus") == []


def test_a_bad_declaration_loses_that_sub_plugin_and_is_logged(monkeypatch, caplog):
    class Broken(Chorus):
        def sub_plugins(self):
            return [
                api.SubPlugin("Upper Case", "Wrong id"),
                api.SubPlugin("ok", "Fine"),
                api.SubPlugin("ok", "Twice"),
                api.SubPlugin("x" * 31, "Too long"),
                "not a sub-plugin",
            ]

    monkeypatch.setattr(
        registry, "_load_third_party", lambda kind: [Broken()] if kind == "source" else []
    )
    with caplog.at_level(logging.ERROR):
        registry.plugins("source", refresh=True)
        found = registry.sub_plugins("chorus")
    registry._cache.pop("source", None)
    assert [entry.name for entry in found] == ["chorus:ok"]
    assert caplog.text.count("Plugin 'chorus'") + caplog.text.count("Sub-plugin") >= 3


def test_a_declaration_that_raises_costs_only_that_plugin(monkeypatch):
    class Raises(Chorus):
        def sub_plugins(self):
            raise RuntimeError("no")

    monkeypatch.setattr(
        registry, "_load_third_party", lambda kind: [Raises()] if kind == "source" else []
    )
    registry.plugins("source", refresh=True)
    assert registry.sub_plugins("chorus") == []
    registry._cache.pop("source", None)


def test_a_sub_plugin_follows_its_declared_default(chorus, user):
    assert policy.decide("chorus:alto", user).on
    bass = policy.decide("chorus:bass", user)
    assert not bass.on and bass.offered and not bass.theirs
    assert bass.decided_by == "sub-default"
    assert "administrator" in str(bass.explain())
    assert api.sub_plugin_enabled("chorus", "alto", user)
    assert not api.sub_plugin_enabled("chorus", "bass", user)
    assert [sub.id for sub in api.enabled_sub_plugins("chorus", user)] == ["alto", "tenor"]


def test_one_that_cannot_work_here_is_off_with_the_reason_whatever_is_decided(chorus, user):
    PluginPolicy.objects.create(
        plugin="chorus:piccolo", person=None, state=PluginPolicy.State.FORCED_ON
    )
    decision = policy.decide("chorus:piccolo", user)
    assert not decision.on
    assert decision.decided_by == "unavailable"
    assert str(decision.explain()) == "Needs the piccolo library."


def test_an_administrators_row_turns_a_default_off_one_on(chorus, user):
    PluginPolicy.objects.create(
        plugin="chorus:bass", person=None, state=PluginPolicy.State.AVAILABLE
    )
    assert policy.decide("chorus:bass", user).on


def test_a_sub_plugin_is_off_while_its_plugin_is_off(chorus, user):
    PluginPolicy.objects.create(plugin="chorus", person=None, state=PluginPolicy.State.FORCED_OFF)
    decision = policy.decide("chorus:alto", user)
    assert not decision.on and decision.decided_by == "parent"
    assert policy.children_of("chorus") == [
        "chorus:alto",
        "chorus:bass",
        "chorus:tenor",
        "chorus:piccolo",
    ]


def test_a_sub_plugin_of_something_installed_is_the_persons_to_switch_not_a_shipped_one(
    chorus, user
):
    assert not policy.shipped_inside("chorus:alto")
    assert policy.decide("chorus:alto", user).theirs


def test_the_page_lists_each_under_its_plugin_and_switches_it_alone(client, admin, user, chorus):
    client.force_login(admin)
    body = client.get(reverse("server:plugins")).content.decode()
    for label in ("Alto", "Bass", "Tenor", "Piccolo"):
        assert label in body
    assert "Needs the piccolo library." in body
    assert body.index("Chorus") < body.index("Alto")

    rows = {row["name"]: row for row in _rows()}
    assert rows["chorus:alto"]["child"] and rows["chorus:alto"]["parent"] == "chorus"
    assert rows["chorus:alto"]["on"] and not rows["chorus:bass"]["on"]

    # Alto off, Bass on: each its own row, and Tenor, untouched, none.
    client.post(
        reverse("server:plugin_policy"),
        {"row:chorus:alto": "1", "row:chorus:bass": "1", "on:chorus:bass": "1"},
    )
    assert (
        PluginPolicy.objects.get(plugin="chorus:alto", person=None).state
        == PluginPolicy.State.FORCED_OFF
    )
    assert (
        PluginPolicy.objects.get(plugin="chorus:bass", person=None).state
        == PluginPolicy.State.AVAILABLE
    )
    assert not PluginPolicy.objects.filter(plugin="chorus:tenor").exists()
    assert not api.sub_plugin_enabled("chorus", "alto", user)
    assert api.sub_plugin_enabled("chorus", "bass", user)

    # Back to the declared defaults: no row is kept for what nobody decided.
    client.post(
        reverse("server:plugin_policy"),
        {"row:chorus:alto": "1", "on:chorus:alto": "1", "row:chorus:bass": "1"},
    )
    assert not PluginPolicy.objects.filter(plugin__startswith="chorus:").exists()


def _rows():
    from postulo.core.server_views import _policy_rows

    return _policy_rows()


def test_the_facade_names_what_a_plugin_needs():
    for name in ("SubPlugin", "enabled_sub_plugins", "sub_plugin_enabled"):
        assert name in api.__all__, name
    from postulo.plugins import base

    assert api.SubPlugin is base.SubPlugin


def test_the_kinds_that_may_offer_them_are_the_kinds_an_administrator_governs():
    from postulo.plugins import base

    assert base.SUB_PLUGIN_KINDS == policy.GOVERNED_KINDS
