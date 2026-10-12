"""The module "Built-in notifications" and its three sub-modules (#876).

The notifiers keep their names -- connections and policy rows key on them -- and what changes
is the label and the icon, and that the module's switch is theirs.
"""

from __future__ import annotations

import pytest

from postulo.plugins import base, policy, registry
from postulo.plugins.models import PluginPolicy

pytestmark = pytest.mark.django_db

MODULE = "builtin-notifications"

#: name -> (label, icon). "Browser notifications" and not "HTML5 Notifications": a person
#: picks it for where it shows, and HTML5 is in the description for the one who asks.
CHILDREN = {
    "browser": ("Browser notifications", "message-square-dot"),
    "email": ("Email notifications", "mail"),
    "webhook": ("Webhooks", "webhook"),
}


def test_the_module_ships_as_a_feature_with_the_bell():
    found = registry.find_plugin("feature", MODULE)
    manifest = base.manifest_of(found)
    assert str(manifest.label) == "Built-in notifications"
    assert manifest.icon == "bell"
    assert not manifest.parent


@pytest.mark.parametrize(("name", "wanted"), CHILDREN.items())
def test_each_notifier_keeps_its_name_and_is_a_sub_module(name, wanted):
    label, icon = wanted
    found = registry.find_plugin("notifier", name)
    assert found is not None
    manifest = base.manifest_of(found)
    assert (manifest.parent, str(manifest.label), manifest.icon) == (MODULE, label, icon)
    assert "notifications" in manifest.categories


def test_the_browser_description_says_html5():
    found = registry.find_plugin("notifier", "browser")
    assert "HTML5" in str(base.description_of(found))


def test_the_module_has_exactly_the_three():
    assert set(policy.children_of(MODULE)) == set(CHILDREN)


def test_own_mail_and_smtp_are_not_in_the_family():
    assert policy.parent_of("own-mail") == ""
    assert policy.parent_of("smtp") == ""


def test_switching_the_module_off_switches_the_three_off(user):
    PluginPolicy.objects.create(plugin=MODULE, state=PluginPolicy.State.FORCED_OFF)
    for name in CHILDREN:
        assert not policy.decide(name, user).on, name
        assert name not in [p.name for p in policy.plugins_for(user, "notifier")]


def test_one_notifier_off_on_its_own_leaves_the_others(user):
    PluginPolicy.objects.create(plugin="webhook", state=PluginPolicy.State.FORCED_OFF)
    names = [p.name for p in policy.plugins_for(user, "notifier")]
    assert "webhook" not in names
    assert {"browser", "email"} <= set(names)
