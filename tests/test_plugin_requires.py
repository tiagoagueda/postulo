"""A plugin may need other plugins, and Postulo's modules (#884).

Three places ask what a plugin needs -- the policy at every request, installing from a
catalogue, and removing -- and they must not disagree: a plugin whose need is missing or off
is off, a release that needs something absent is refused before it is downloaded, and
something others need cannot be removed from under them.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from postulo.plugins import base, catalogue, dependencies, installing, policy, registry
from postulo.plugins.api import declares, shipped
from postulo.plugins.models import PluginPolicy

pytestmark = pytest.mark.django_db


def _feature(name, **extra):
    @declares(shipped(name=name, label=name.title(), kind="feature", description=name, **extra))
    class Feature:
        pass

    return Feature


@pytest.fixture
def pair():
    """`needy` needs `base-one`, and `lonely` needs something that is not there."""
    classes = [
        _feature("base-one"),
        _feature("needy", requires=("base-one",)),
        _feature("lonely", requires=("nothing-by-this-name>=1",)),
    ]
    for item in classes:
        registry.register_builtin("feature", item)
    try:
        yield
    finally:
        for item in classes:
            registry.unregister_builtin("feature", item)


# ------------------------------------------------------------------- parsing


def test_a_requirement_is_a_name_and_a_specifier():
    needs = dependencies.parse(["maps", "helloworld>=0.3,<1", "not a requirement !!"])
    assert [(n.name, n.specifier) for n in needs] == [("maps", ""), ("helloworld", "<1,>=0.3")]


def test_requires_is_a_tuple_of_names_not_one_string():
    with pytest.raises(TypeError):
        base.Manifest(name="x", requires="maps")
    assert base.Manifest(name="x", requires=["maps"]).requires == ("maps",)


def test_a_module_that_ships_is_present_at_the_version_of_postulo():
    assert dependencies.is_present(dependencies.Need("maps"))
    assert dependencies.is_present(dependencies.Need("maps", ">=0.1"))
    assert not dependencies.is_present(dependencies.Need("maps", ">=999"))
    assert not dependencies.is_present(dependencies.Need("not-a-plugin"))


# -------------------------------------------------------------------- policy


def test_a_plugin_is_on_while_what_it_needs_is_on(user, pair):
    assert policy.decide("needy", user).on


def test_a_plugin_is_off_when_what_it_needs_is_switched_off(user, pair):
    PluginPolicy.objects.create(plugin="base-one", state=PluginPolicy.State.FORCED_OFF)
    decision = policy.decide("needy", user)
    assert not decision.on and not decision.theirs
    assert decision.decided_by == "requires"
    assert "Base-One" in decision.explain()


def test_a_plugin_is_off_when_what_it_needs_is_not_installed(user, pair):
    decision = policy.decide("lonely", user)
    assert not decision.on
    assert decision.because == ("nothing-by-this-name",)


def test_a_need_switched_off_for_one_person_is_off_for_them_alone(user, pair, django_user_model):
    other = django_user_model.objects.create_user(
        email="o@example.org", password="a-fairly-long-password-42", username="other"
    )
    PluginPolicy.objects.create(plugin="base-one", person=user, state=PluginPolicy.State.FORCED_OFF)
    assert not policy.decide("needy", user).on
    assert policy.decide("needy", other).on


def test_two_plugins_that_need_each_other_do_not_loop(user):
    first = _feature("loop-a", requires=("loop-b",))
    second = _feature("loop-b", requires=("loop-a",))
    for item in (first, second):
        registry.register_builtin("feature", item)
    try:
        assert isinstance(policy.decide("loop-a", user).on, bool)
    finally:
        for item in (first, second):
            registry.unregister_builtin("feature", item)


def test_nothing_that_ships_needs_itself_or_a_cycle():
    seen = {}
    for classes in registry.builtins().values():
        for item in classes:
            manifest = getattr(item, "manifest", None)
            if isinstance(manifest, base.Manifest):
                seen[manifest.name] = [n.name for n in dependencies.parse(manifest.requires)]
    for name, needs in seen.items():
        for need in needs:
            assert need in seen, f"{name} needs {need}, which does not ship"
            assert name not in seen[need], f"{name} and {need} need each other"


# ------------------------------------------------------------ the page


def test_the_page_takes_the_switch_away_and_says_why(client, admin_user, pair):
    PluginPolicy.objects.create(plugin="base-one", state=PluginPolicy.State.FORCED_OFF)
    client.force_login(admin_user)
    body = client.get(reverse("server:plugins")).content.decode()
    assert 'data-held-by-parent="needy"' in body
    assert "which is off or not installed" in body
    assert 'name="row:needy"' not in body


# --------------------------------------------------------- installing


def test_a_release_that_needs_something_absent_is_refused_naming_it():
    listing = catalogue.Listing(name="postulo-top")
    release = catalogue.Release(
        version="1.0", url="u", sha256="a", requires=("postulo-bottom>=1.0", "maps")
    )
    with pytest.raises(catalogue.CatalogueError) as error:
        catalogue.refuse_unless_it_has_what_it_needs(listing, release)
    assert "postulo-bottom>=1.0" in str(error.value)
    assert "maps" not in str(error.value), "a module that ships is not missing"


def test_a_release_that_needs_only_what_is_here_goes_ahead():
    release = catalogue.Release(version="1.0", url="u", sha256="a", requires=("maps",))
    catalogue.refuse_unless_it_has_what_it_needs(catalogue.Listing(name="x"), release)


def test_the_requires_of_a_release_is_read_from_the_index():
    payload = (
        b'{"plugins": [{"name": "postulo-top", "releases": [{"version": "1.0", "url": "u",'
        b' "sha256": "' + b"a" * 64 + b'", "requires": ["postulo-bottom>=1"]}]}]}'
    )
    listings = catalogue.parse(payload, catalogue="test")
    assert listings[0].releases[0].requires == ("postulo-bottom>=1",)


def test_what_a_release_needs_is_kept_in_the_record(tmp_path, settings):
    settings.POSTULO_PLUGINS_DIR = tmp_path / "plugins"
    installing.write_record(
        [installing.Installed(name="postulo-top", version="1", requires=["postulo-bottom>=1"])]
    )
    assert installing.read_record()[0].requires == ["postulo-bottom>=1"]


# ---------------------------------------------------------------- removing


def test_removing_what_another_package_needs_is_refused_naming_it(tmp_path, settings):
    settings.POSTULO_PLUGINS_DIR = tmp_path / "plugins"
    installing.write_record(
        [
            installing.Installed(name="postulo-top", version="1", requires=["postulo-bottom"]),
            installing.Installed(name="postulo-bottom", version="1"),
        ]
    )
    with pytest.raises(installing.InstallError, match="needed by postulo-top"):
        installing.remove("postulo-bottom")
    assert {e.name for e in installing.read_record()} == {"postulo-top", "postulo-bottom"}


def test_removing_the_one_that_needs_is_not_refused(tmp_path, settings):
    settings.POSTULO_PLUGINS_DIR = tmp_path / "plugins"
    installing.write_record(
        [
            installing.Installed(name="postulo-top", version="1", requires=["postulo-bottom"]),
            installing.Installed(name="postulo-bottom", version="1"),
        ]
    )
    assert dependencies.refuse_removing("postulo-top") == ""
    assert dependencies.dependents_of("postulo-bottom") == ["postulo-top"]
