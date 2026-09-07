"""Where plugins may come from, as rows an administrator manages.

Catalogues already worked — a signed index, an Ed25519 key, a checksum per wheel, several
of them supported. What did not exist was any way to manage one: they came from
`POSTULO_PLUGIN_CATALOGUES` as `name|url|key`, so adding a catalogue meant editing a file
and restarting the container.

The environment still wins, and a row that is switched off or missing its key is simply
never offered. Those two are the ones worth guarding: the first because an operator who
pinned a catalogue did so to have it not change from a web page, and the second because
`catalogue.py` is blunt about what a key is for — *"an unsigned list of URLs to run code
from is not something Postulo will offer"*.
"""

from __future__ import annotations

import base64

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from django.contrib.auth import get_user_model
from django.urls import reverse

from postulo.plugins import catalogue
from postulo.plugins.models import PluginRepository

pytestmark = pytest.mark.django_db

User = get_user_model()
PASSWORD = "a-fairly-long-password-42"


def a_key() -> str:
    raw = Ed25519PrivateKey.generate().public_key().public_bytes_raw()
    return base64.b64encode(raw).decode("ascii")


@pytest.fixture
def admin(db):
    return User.objects.create_user(
        email="admin@example.org", password=PASSWORD, username="admin-one", is_staff=True
    )


# ------------------------------------------------------------- what ships


def test_an_instance_starts_with_an_official_row_that_points_nowhere():
    """Postulo publishes no catalogue, and a heading with nothing under it says less than
    a row that says so."""
    official = PluginRepository.objects.get(tier=PluginRepository.Tier.OFFICIAL)
    assert official.url == "" and official.public_key == ""
    assert not official.enabled
    assert not official.usable


def test_there_can_only_ever_be_one_official():
    from django.db import IntegrityError

    with pytest.raises(IntegrityError):
        PluginRepository.objects.create(name="another", tier=PluginRepository.Tier.OFFICIAL)


# ----------------------------------------------------- what may be installed from


def test_a_row_with_an_address_and_a_key_is_offered():
    PluginRepository.objects.create(
        name="mine", url="https://example.org/i.json", public_key=a_key()
    )
    assert "mine" in catalogue.configured()


@pytest.mark.parametrize(
    "changes,why",
    [
        ({"enabled": False}, "switched off"),
        ({"public_key": ""}, "no key, so nothing can be verified"),
        ({"url": ""}, "no address to fetch"),
    ],
)
def test_a_row_that_cannot_be_trusted_is_not_offered(changes, why):
    fields = {"name": "mine", "url": "https://example.org/i.json", "public_key": a_key()}
    PluginRepository.objects.create(**{**fields, **changes})
    assert "mine" not in catalogue.configured(), why


def test_switching_one_off_does_not_touch_what_it_installed(tmp_path, settings):
    """The behaviour the page has to describe, or "off" reads as "gone".

    A plugin's code is on the volume and the registry never consults a catalogue, so what
    stops is installing and updating — not running.
    """
    from postulo.plugins import installing

    settings.POSTULO_PLUGINS_DIR = tmp_path / "plugins"
    installing.write_record(
        [installing.Installed(name="postulo-thing", version="1.0", origin="catalogue:mine")]
    )
    row = PluginRepository.objects.create(
        name="mine", url="https://example.org/i.json", public_key=a_key()
    )

    row.enabled = False
    row.save()

    assert "mine" not in catalogue.configured()
    assert installing.read_record()[0].name == "postulo-thing"
    assert not installing.read_record()[0].disabled


# ------------------------------------------------------- the environment wins


def test_the_environment_beats_a_row(settings):
    PluginRepository.objects.create(
        name="mine", url="https://example.org/row.json", public_key=a_key()
    )
    settings.POSTULO_PLUGIN_CATALOGUES = f"mine|https://example.org/env.json|{a_key()}"

    assert catalogue.configured()["mine"]["url"] == "https://example.org/env.json"
    assert catalogue.pinned_names() == {"mine"}


def test_a_pinned_repository_cannot_be_changed_from_the_page(client, admin, settings):
    """An operator who pinned a catalogue did so to have it not change from a web page."""
    row = PluginRepository.objects.create(
        name="mine", url="https://example.org/row.json", public_key=a_key()
    )
    settings.POSTULO_PLUGIN_CATALOGUES = f"mine|https://example.org/env.json|{a_key()}"
    client.force_login(admin)

    client.post(reverse("server:plugin_repository"), {"name": "mine", "action": "disable"})

    row.refresh_from_db()
    assert row.enabled, "the environment's repository was switched off from the interface"


def test_an_unpinned_row_still_switches(client, admin):
    row = PluginRepository.objects.create(
        name="mine", url="https://example.org/i.json", public_key=a_key()
    )
    client.force_login(admin)

    client.post(reverse("server:plugin_repository"), {"name": "mine", "action": "disable"})

    row.refresh_from_db()
    assert not row.enabled


# ------------------------------------------------------------------- the key


def test_a_key_that_is_not_a_key_is_refused(client, admin):
    client.force_login(admin)

    client.post(
        reverse("server:plugin_repository"),
        {
            "action": "add",
            "name": "wrong",
            "url": "https://example.org/i.json",
            "public_key": "not base64 at all",
            "enabled": "on",
        },
    )

    assert not PluginRepository.objects.filter(name="wrong").exists()


def test_a_replaced_key_is_said_out_loud(client, admin, caplog):
    """Not an edit like changing a label: it replaces the only thing standing between an
    index and code running here."""
    PluginRepository.objects.create(
        name="mine", url="https://example.org/i.json", public_key=a_key()
    )
    client.force_login(admin)

    response = client.post(
        reverse("server:plugin_repository"),
        {
            "action": "save",
            "name": "mine",
            "url": "https://example.org/i.json",
            "public_key": a_key(),
            "enabled": "on",
        },
        follow=True,
    )

    assert b"key for" in response.content
    assert any("public key replaced" in record.getMessage() for record in caplog.records)


# ------------------------------------------------------------- who may do it


@pytest.mark.parametrize("action", ["add", "save", "enable", "disable", "remove"])
def test_only_an_administrator_reaches_any_of_it(client, action):
    person = User.objects.create_user(
        email="someone@example.org", password=PASSWORD, username="someone"
    )
    PluginRepository.objects.create(
        name="mine", url="https://example.org/i.json", public_key=a_key()
    )
    client.force_login(person)

    response = client.post(reverse("server:plugin_repository"), {"name": "mine", "action": action})

    assert response.status_code in {302, 403, 404}
    assert PluginRepository.objects.filter(name="mine").exists()


def test_the_official_row_is_emptied_rather_than_removed(client, admin):
    client.force_login(admin)

    client.post(reverse("server:plugin_repository"), {"name": "official", "action": "remove"})

    assert PluginRepository.objects.filter(tier=PluginRepository.Tier.OFFICIAL).exists()


# ------------------------------------------------------------------ the page


def test_the_page_shows_every_tier_including_the_one_with_no_row(client, admin):
    PluginRepository.objects.create(
        name="mine", url="https://example.org/i.json", public_key=a_key()
    )
    client.force_login(admin)

    html = client.get(reverse("server:plugins")).content.decode()

    assert 'data-repository="internal"' in html, "the built-ins are a repository on this page"
    assert 'data-repository="official"' in html
    assert 'data-repository="mine"' in html


def test_the_internal_row_offers_nothing_to_press(client, admin):
    """It cannot be switched off, because that would mean switching off Postulo."""
    import re

    client.force_login(admin)
    html = client.get(reverse("server:plugins")).content.decode()

    row = re.search(r'<li[^>]*data-repository="internal".*?</li>', html, re.S).group(0)
    assert "<button" not in row
