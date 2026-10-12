"""A package is the plugin and its entry points are sub-plugins; listings have categories (#883)."""

from __future__ import annotations

import pytest
from django.urls import reverse

from postulo.plugins import catalogue, dependencies, installing, registry
from postulo.plugins.api import declares, shipped
from postulo.plugins.models import PluginPolicy

pytestmark = pytest.mark.django_db


def _feature(name):
    @declares(shipped(name=name, label=name.title(), kind="feature", description=name))
    class Feature:
        pass

    return Feature


@pytest.fixture
def package(tmp_path, settings, monkeypatch):
    """`postulo-duo` registers two plugins, `duo-one` and `duo-two`."""
    settings.POSTULO_PLUGINS_DIR = tmp_path / "plugins"
    classes = [_feature("duo-one"), _feature("duo-two")]
    for item in classes:
        registry.register_builtin("feature", item)
    installing.write_record(
        [
            installing.Installed(
                name="postulo-duo",
                version="1.0",
                entry_points=["postulo.features:duo-one", "postulo.features:duo-two"],
            )
        ]
    )
    monkeypatch.setattr(dependencies, "provided_by", lambda name: {"duo-one", "duo-two"})
    try:
        yield "postulo-duo"
    finally:
        for item in classes:
            registry.unregister_builtin("feature", item)


def row_of(body, name):
    start = body.index(f'data-policy="{name}"')
    return body[start : body.index("</li>", start)]


def test_a_package_with_several_entry_points_is_a_parent_row(client, admin_user, package):
    client.force_login(admin_user)
    body = client.get(reverse("server:plugins")).content.decode()
    assert body.index('data-policy="postulo-duo"') < body.index('data-policy="duo-one"')
    assert "data-has-children" in row_of(body, "postulo-duo")
    assert 'data-parent="postulo-duo"' in row_of(body, "duo-two")
    assert 'name="pkg-on:postulo-duo"' in row_of(body, "postulo-duo")


def test_a_package_with_one_entry_point_is_one_row(client, admin_user, package, tmp_path):
    installing.write_record(
        [
            installing.Installed(
                name="postulo-duo", version="1", entry_points=["postulo.features:duo-one"]
            )
        ]
    )
    client.force_login(admin_user)
    body = client.get(reverse("server:plugins")).content.decode()
    assert 'data-policy="postulo-duo"' not in body
    assert "data-parent=" not in row_of(body, "duo-one")


def test_the_packages_switch_is_the_installed_lists(client, admin_user, package):
    client.force_login(admin_user)
    client.post(reverse("server:plugin_policy"), {"pkg-row:postulo-duo": "1"})
    assert installing.installed("postulo-duo").disabled
    client.post(
        reverse("server:plugin_policy"), {"pkg-row:postulo-duo": "1", "pkg-on:postulo-duo": "on"}
    )
    assert not installing.installed("postulo-duo").disabled


def test_a_catalogue_listing_carries_its_categories():
    payload = (
        b'{"plugins": [{"name": "postulo-x", "categories": ["files", "unheard-of"], '
        b'"releases": [{"version": "1", "url": "u", "sha256": "' + b"a" * 64 + b'"}]}]}'
    )
    assert catalogue.parse(payload, catalogue="t")[0].categories == ("files", "unheard-of")


def test_a_listing_without_categories_has_none():
    payload = b'{"plugins": [{"name": "postulo-x", "releases": []}]}'
    assert catalogue.parse(payload, catalogue="t")[0].categories == ()


def test_the_catalogues_list_shows_categories_and_filters_by_them(client, admin_user, monkeypatch):
    monkeypatch.setattr(catalogue, "configured", lambda: {"c": {"url": "u", "key": "k"}})
    client.force_login(admin_user)
    session = client.session
    session["plugin_listings"] = [
        {
            "name": "alpha",
            "version": "1",
            "catalogue": "c",
            "compatible": True,
            "categories": ["files"],
        },
        {
            "name": "beta",
            "version": "1",
            "catalogue": "c",
            "compatible": True,
            "categories": ["calendar"],
        },
    ]
    session.save()
    everything = client.get(reverse("server:plugins")).content.decode()
    assert "alpha" in everything and "beta" in everything
    assert 'data-category="files"' in everything
    only = client.get(reverse("server:plugins"), {"category": "files"}).content.decode()
    assert "alpha" in only
    assert "beta 1" not in only


def test_the_policy_still_answers_for_the_sub_plugins(user, package):
    from postulo.plugins import policy

    PluginPolicy.objects.create(plugin="duo-one", state=PluginPolicy.State.FORCED_OFF)
    assert not policy.decide("duo-one", user).on
    assert policy.decide("duo-two", user).on
