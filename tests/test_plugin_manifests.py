"""Every plugin says who it is, and the ones Postulo ships say it first (#98).

#97 settled what a plugin declares. This is the half that applies it to Postulo's own, on
the principle that a requirement this project asks of other people and not of itself is not
a requirement. The interesting test is the last one: it walks the built-ins rather than
naming them, so a plugin added later cannot quietly skip the declaration.

The shape changed with it, on the maintainer's suggestion: the facts live in one `Manifest`
rather than one optional attribute each. That is not decoration. A loose attribute per fact
can never be *required* — `runtime_checkable` protocols check data members, and `registry`
drops a plugin that fails `isinstance`, so adding `label` to `SourcePlugin` would have
silently unloaded every source anybody had already written. One optional attribute carrying
any number of facts has neither problem.
"""

from __future__ import annotations

import pytest
from django.utils.html import escape

from postulo import __version__
from postulo.plugins import registry
from postulo.plugins.base import (
    SHIPPED_AUTHOR,
    SHIPPED_LICENCE,
    SHIPPED_SOURCE_URL,
    Manifest,
    declares,
    description_of,
    label_of,
    manifest_of,
    shipped,
)

#: The identifier of a source is written into every capture's `source` field, so renaming
#: one orphans the history of every capture anybody made with it. Pinned here because this
#: is the file somebody would be editing when they were tempted.
NAMES_THAT_ARE_IN_PEOPLES_DATA = {"schema.org", "page-metadata"}


# ------------------------------------------------------------------ the manifest


def test_a_manifest_needs_a_name():
    with pytest.raises(ValueError):
        Manifest(name="")


def test_a_plugin_with_no_words_for_a_name_gets_its_identifier():
    assert Manifest(name="page-metadata").label == "page-metadata"


def test_declaring_a_manifest_satisfies_the_protocol_from_it():
    """One source of truth: the attributes the registry reads come off the manifest.

    Writing them out beside a manifest that repeated them would be two places to change one
    fact, and the day they disagree is the day a capture is filed against a plugin that does
    not exist.
    """

    @declares(Manifest(name="demo", label="Demo", version="9.9", kind="source"))
    class Demo:
        pass

    assert (Demo.name, Demo.label, Demo.version, Demo.kind) == ("demo", "Demo", "9.9", "source")
    assert Demo().manifest.name == "demo"


def test_a_plugin_that_never_heard_of_manifests_still_reads_back():
    """The compatibility that matters: third-party plugins written before any of this."""

    class Old:
        name = "old-timer"
        version = "1.4"
        kind = "notifier"
        label = "Old Timer"

    manifest = manifest_of(Old())

    assert manifest.name == "old-timer"
    assert manifest.label == "Old Timer"
    assert manifest.version == "1.4"
    assert label_of(Old()) == "Old Timer" and description_of(Old()) == ""


def test_a_plugin_that_says_nothing_at_all_gets_its_identifier_back():
    class Bare:
        name = "bare"

    assert label_of(Bare()) == "bare"
    assert description_of(Bare()) == ""


# ---------------------------------------------------- what Postulo's own must say


def test_shipped_takes_postulos_version_rather_than_inventing_one():
    """`version = "1.0"` meant 1.0 the day it was written and identified nothing after."""
    manifest = shipped(name="x", label="X", kind="source", description="d")

    assert manifest.version == __version__
    assert manifest.author == SHIPPED_AUTHOR
    assert manifest.licence == SHIPPED_LICENCE
    assert manifest.source_url == SHIPPED_SOURCE_URL


def test_every_plugin_postulo_ships_declares_the_full_set():
    """Walked, not listed.

    They are registered from three app configs and a module-level list, so "all of them" is
    easy to miscount by hand — the issue that asked for this said four and there are six.
    A seventh added later is caught here rather than noticed later.
    """
    missing = []
    for kind, classes in registry.builtins().items():
        for plugin_class in classes:
            manifest = manifest_of(plugin_class())
            for field in (
                "name",
                "label",
                "version",
                "description",
                "author",
                "licence",
                "source_url",
            ):
                if not getattr(manifest, field):
                    missing.append(f"{kind}/{manifest.name}: no {field}")
            if manifest.kind != kind:
                missing.append(f"{kind}/{manifest.name}: calls itself a {manifest.kind!r}")
            if manifest.version != __version__:
                missing.append(
                    f"{kind}/{manifest.name}: version {manifest.version!r} is not Postulo's"
                )

    assert not missing, "\n".join(missing)


def test_there_are_ten_of_them_across_seven_kinds():
    """Named rather than counted, so that losing one to a bad import is a failure rather
    than a quiet absence — and so that adding one is a line somebody wrote.

    Three govern something other than a service. `phone-numbers` and `email-addresses`
    govern a page -- the second owns no data at all, since the addresses are allauth's, which
    is the honest limit of what a feature can be here (#145). `postal-rules` governs a table:
    what a country expects of an address, and what it calls each part (#147).
    """
    found = {
        manifest_of(plugin_class()).name
        for classes in registry.builtins().values()
        for plugin_class in classes
    }

    assert found == {
        "schema.org",
        "page-metadata",
        "email",
        "local",
        "europass",
        "smtp",
        "own-mail",
        "phone-numbers",
        "email-addresses",
        "postal-rules",
    }


def test_the_identifiers_that_are_in_peoples_data_have_not_moved():
    """Renaming a source orphans the history of every capture made with it."""
    names = {manifest_of(plugin_class()).name for plugin_class in registry.builtins()["source"]}

    assert NAMES_THAT_ARE_IN_PEOPLES_DATA <= names


# ---------------------------------------------------------------- shown, not stored


@pytest.mark.django_db
def test_the_administrators_page_says_who_wrote_each_plugin(client, django_user_model):
    """#97's complaint: visible on the day of installation and never again."""
    admin = django_user_model.objects.create_user(
        email="admin@example.org",
        username="admin",
        password="a-long-enough-password-42",
        is_staff=True,
        is_superuser=True,
    )
    client.force_login(admin)

    html = client.get("/server/plugins/").content.decode()

    assert 'data-provenance="schema.org"' in html
    # Escaped, because an author is `Name <address>` and the angle brackets are not markup.
    assert escape(SHIPPED_AUTHOR) in html and SHIPPED_AUTHOR not in html
    assert SHIPPED_LICENCE in html
    assert SHIPPED_SOURCE_URL in html
    assert __version__ in html
