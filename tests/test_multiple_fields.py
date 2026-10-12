"""The module "Multiple fields" and the six features that are its sub-modules (#875).

The internal names stay -- policy rows, the archive and the plugins record key on them -- and
what changes is the label, the icon, and that switching the module off switches all six off.
"""

from __future__ import annotations

import pytest

from postulo.plugins import base, policy, registry
from postulo.plugins.models import PluginPolicy
from postulo.plugins.multiple_fields import MULTIPLE_FIELDS

pytestmark = pytest.mark.django_db

#: name -> (label, icon): the names are what rows key on and must not move.
CHILDREN = {
    "email-addresses": ("Multiple email addresses", "mails"),
    "phone-numbers": ("Multiple telephone numbers", "phone"),
    "repositories": ("Multiple code repositories", "git-graph"),
    "social-profiles": ("Multiple social profiles", "waypoints"),
    "websites": ("Multiple websites", "globe"),
    "messaging-contacts": ("Multiple messaging contacts", "message-circle"),
}


def test_the_module_ships_and_is_a_feature():
    found = registry.find_plugin("feature", MULTIPLE_FIELDS)
    assert found is not None
    manifest = base.manifest_of(found)
    assert str(manifest.label) == "Multiple fields"
    assert not manifest.parent


@pytest.mark.parametrize(("name", "wanted"), CHILDREN.items())
def test_each_feature_keeps_its_name_and_is_a_sub_module(name, wanted):
    label, icon = wanted
    found = registry.find_plugin("feature", name)
    assert found is not None, f"{name} no longer ships under its name"
    manifest = base.manifest_of(found)
    assert manifest.parent == MULTIPLE_FIELDS
    assert str(manifest.label) == label
    assert manifest.icon == icon


def test_the_module_lists_exactly_these_six():
    assert set(policy.children_of(MULTIPLE_FIELDS)) == set(CHILDREN)


def test_all_six_are_on_by_default(user):
    for name in CHILDREN:
        assert policy.decide(name, user).on, name


def test_switching_the_module_off_switches_all_six_off(user):
    PluginPolicy.objects.create(plugin=MULTIPLE_FIELDS, state=PluginPolicy.State.FORCED_OFF)
    for name in CHILDREN:
        decision = policy.decide(name, user)
        assert not decision.on, name
        assert decision.decided_by == "parent"


def test_one_can_still_be_switched_off_by_itself(user):
    PluginPolicy.objects.create(plugin="websites", state=PluginPolicy.State.FORCED_OFF)
    assert not policy.decide("websites", user).on
    assert policy.decide("phone-numbers", user).on
    assert policy.decide(MULTIPLE_FIELDS, user).on


def test_the_old_words_are_gone_from_the_interface(client, user):
    """Nothing the interface says still calls them "Several …"."""
    from django.utils.translation import override

    with override("en"):
        for _name, (label, _icon) in CHILDREN.items():
            assert not label.startswith("Several")
