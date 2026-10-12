"""A module and its sub-modules: the parent decides first (#873).

What ships inside Postulo is a module, and a module may have sub-modules, each with exactly
one parent. These check the cascade the switches rely on -- a child of a parent that is off
is off and not the person's to move -- and that the administrator's page draws it and saves it
in the order the issue asks: switching a parent on brings every child with it, and a child
whose control the page took away is left as it was.
"""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from postulo.plugins import base, categories, policy, registry
from postulo.plugins.api import declares, shipped
from postulo.plugins.models import PluginPolicy

pytestmark = pytest.mark.django_db

User = get_user_model()
PASSWORD = "a-fairly-long-password-42"


def _feature(name, label, **extra):
    @declares(shipped(name=name, label=label, kind="feature", description=label, **extra))
    class Feature:
        pass

    return Feature


@pytest.fixture
def family():
    """A parent and two children, registered for the test and taken away after it."""
    parent = _feature("test-parent", "Test parent", categories=("contacts",))
    first = _feature("test-first", "Test first", parent="test-parent")
    second = _feature("test-second", "Test second", parent="test-parent")
    classes = [parent, first, second]
    for item in classes:
        registry.register_builtin("feature", item)
    try:
        yield "test-parent", "test-first", "test-second"
    finally:
        for item in classes:
            registry.unregister_builtin("feature", item)


@pytest.fixture
def admin():
    return User.objects.create_user(
        email="admin@example.org", password=PASSWORD, username="admin-one", is_staff=True
    )


def test_a_plugin_cannot_be_its_own_parent():
    with pytest.raises(ValueError):
        base.Manifest(name="loop", parent="loop")


def test_categories_are_a_tuple_of_slugs():
    with pytest.raises(TypeError):
        base.Manifest(name="x", categories="contacts")
    assert base.Manifest(name="x", categories=["contacts"]).categories == ("contacts",)


def test_a_child_is_on_with_its_parent(user, family):
    parent, first, _second = family
    assert policy.decide(parent, user).on
    assert policy.decide(first, user).on


def test_a_child_of_a_parent_that_is_off_is_off_and_not_theirs(user, family):
    parent, first, second = family
    PluginPolicy.objects.create(plugin=parent, state=PluginPolicy.State.FORCED_OFF)
    for name in (first, second):
        decision = policy.decide(name, user)
        assert not decision.on and not decision.theirs
        assert decision.decided_by == "parent"
        assert decision.explain()


def test_a_child_row_does_not_beat_its_parent(user, family):
    """A child forced on is still off while its parent is: the parent is asked first."""
    parent, first, _second = family
    PluginPolicy.objects.create(plugin=parent, state=PluginPolicy.State.FORCED_OFF)
    PluginPolicy.objects.create(plugin=first, state=PluginPolicy.State.FORCED_ON)
    assert not policy.decide(first, user).on


def test_a_parent_off_for_one_person_is_off_for_them_alone(user, family, django_user_model):
    parent, first, _second = family
    other = django_user_model.objects.create_user(
        email="o@example.org", password=PASSWORD, username="other"
    )
    PluginPolicy.objects.create(plugin=parent, person=user, state=PluginPolicy.State.FORCED_OFF)
    assert not policy.decide(first, user).on
    assert policy.decide(first, other).on


def test_a_child_off_on_its_own_leaves_the_parent_and_its_sibling(user, family):
    parent, first, second = family
    PluginPolicy.objects.create(plugin=first, state=PluginPolicy.State.FORCED_OFF)
    assert policy.decide(parent, user).on
    assert not policy.decide(first, user).on
    assert policy.decide(second, user).on


def test_children_are_found_by_their_parent(family):
    parent, first, second = family
    assert policy.children_of(parent) == [first, second]
    assert policy.parent_of(first) == parent
    assert policy.parent_of(parent) == ""


def _post(client, **fields):
    return client.post(reverse("server:plugin_policy"), fields)


def test_the_page_nests_the_children_under_their_parent(client, admin, family):
    parent, first, second = family
    client.force_login(admin)
    body = client.get(reverse("server:plugins")).content.decode()
    at = [body.index(f'data-policy="{name}"') for name in (parent, first, second)]
    assert at == sorted(at)
    assert body.index(f'data-parent="{parent}"') > at[0]
    assert 'data-category="contacts"' in body
    assert str(categories.LABELS["contacts"]) in body


def test_the_page_takes_a_childs_controls_away_while_its_parent_is_off(client, admin, family):
    parent, first, _second = family
    PluginPolicy.objects.create(plugin=parent, state=PluginPolicy.State.FORCED_OFF)
    client.force_login(admin)
    body = client.get(reverse("server:plugins")).content.decode()
    assert f'name="row:{parent}"' in body
    assert f'name="row:{first}"' not in body
    assert f'data-held-by-parent="{first}"' in body


def test_a_child_left_out_of_the_form_is_not_read_as_off(client, admin, family):
    """A disabled control submits nothing, and absence must not become a decision."""
    parent, first, _second = family
    PluginPolicy.objects.create(plugin=parent, state=PluginPolicy.State.FORCED_OFF)
    client.force_login(admin)
    _post(client, **{f"row:{parent}": "1", f"free:{parent}": "1"})
    assert not PluginPolicy.objects.filter(plugin=first).exists()


def test_switching_a_parent_on_clears_its_childrens_off_rows(client, admin, family):
    parent, first, second = family
    PluginPolicy.objects.create(plugin=parent, state=PluginPolicy.State.FORCED_OFF)
    PluginPolicy.objects.create(plugin=first, state=PluginPolicy.State.FORCED_OFF)
    PluginPolicy.objects.create(plugin=second, state=PluginPolicy.State.FORCED_OFF)
    client.force_login(admin)
    _post(client, **{f"row:{parent}": "1", f"on:{parent}": "on", f"free:{parent}": "on"})
    assert not PluginPolicy.objects.filter(plugin__in=[parent, first, second]).exists()


def test_after_that_a_child_can_be_switched_off_by_itself(client, admin, family):
    parent, first, second = family
    client.force_login(admin)
    _post(
        client,
        **{
            f"row:{parent}": "1",
            f"on:{parent}": "on",
            f"free:{parent}": "on",
            f"row:{first}": "1",
            f"free:{first}": "on",
            f"row:{second}": "1",
            f"on:{second}": "on",
            f"free:{second}": "on",
        },
    )
    assert PluginPolicy.objects.get(plugin=first).state == PluginPolicy.State.FORCED_OFF
    assert not PluginPolicy.objects.filter(plugin=second).exists()


def test_the_persons_page_nests_too(client, admin, family):
    parent, first, _second = family
    person = User.objects.create_user(email="p@example.org", password=PASSWORD, username="pat")
    PluginPolicy.objects.create(plugin=parent, person=person, state=PluginPolicy.State.FORCED_OFF)
    client.force_login(admin)
    body = client.get(reverse("server:person_plugins", args=[person.pk])).content.decode()
    assert f'data-parent="{parent}"' in body
    assert f'name="row:{first}"' not in body


# --------------------------------------------- what Postulo ships declares a tree


def _every_shipped():
    return [
        plugin_class
        for classes in registry.builtins().values()
        for plugin_class in classes
        if isinstance(getattr(plugin_class, "manifest", None), base.Manifest)
    ]


def test_every_parent_that_ships_is_a_feature_without_a_parent_of_its_own():
    names = {item.manifest.name: item for item in _every_shipped()}
    for item in names.values():
        parent = item.manifest.parent
        if not parent:
            continue
        assert parent in names, f"{item.manifest.name} names a parent that does not ship: {parent}"
        assert names[parent].manifest.kind == "feature", parent
        assert not names[parent].manifest.parent, f"{parent} is a child and a parent"


def test_every_category_that_ships_has_words():
    for item in _every_shipped():
        for slug in item.manifest.categories:
            assert slug in categories.LABELS, f"{item.manifest.name}: unknown category {slug}"
