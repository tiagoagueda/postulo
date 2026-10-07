"""The official catalogue: the list of plugins the project publishes, signed, and switchable.

``catalogue/official/index.json`` and its ``.sig`` are what an instance fetches when an
administrator switches the *official* repository on. These tests hold the committed pair to
what an instance would check, the row to the address it should point at, and the switch to
doing nothing until it is pressed.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import httpx
import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from postulo.plugins import catalogue, installing, provenance
from postulo.plugins.models import PluginRepository

pytestmark = pytest.mark.django_db

ROOT = Path(__file__).resolve().parent.parent
DIRECTORY = ROOT / "catalogue" / "official"
PASSWORD = "a-fairly-long-password-42"


def load_tool():
    spec = importlib.util.spec_from_file_location(
        "official_catalogue", ROOT / "scripts" / "official_catalogue.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def admin(db):
    return get_user_model().objects.create_user(
        email="admin@example.org", password=PASSWORD, username="admin-one", is_staff=True
    )


@pytest.fixture
def official() -> PluginRepository:
    return PluginRepository.objects.get(tier=PluginRepository.Tier.OFFICIAL)


@pytest.fixture
def published(monkeypatch):
    """The committed index and signature, served from where the row says they are."""
    served = {
        provenance.OFFICIAL_URL: (DIRECTORY / "index.json").read_bytes(),
        provenance.OFFICIAL_URL + ".sig": (DIRECTORY / "index.json.sig").read_bytes(),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        body = served.get(str(request.url))
        return httpx.Response(200, content=body) if body is not None else httpx.Response(404)

    def client(**kwargs):
        kwargs.pop("event_hooks", None)
        kwargs.setdefault("timeout", 10)
        return httpx.Client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(catalogue.http, "client", client)
    return served


# ------------------------------------------------------------ the committed pair


def test_the_committed_index_is_the_one_its_signature_covers():
    """What an instance checks first, run against what is in the repository."""
    assert load_tool().check(None) == []


def test_a_changed_index_is_refused(official, published):
    official.enabled = True
    official.save()
    published[provenance.OFFICIAL_URL] = published[provenance.OFFICIAL_URL].replace(
        b"postulo-helloworld", b"postulo-hellowurld"
    )

    with pytest.raises(catalogue.CatalogueError, match="signature does not match"):
        catalogue.fetch("official")


def test_the_index_lists_what_the_source_names_and_no_more():
    import tomllib

    source = tomllib.loads((DIRECTORY / "plugins.toml").read_text(encoding="utf-8"))
    index = json.loads((DIRECTORY / "index.json").read_bytes())

    wanted = {
        entry["name"]: [(release["version"], release["url"]) for release in entry["release"]]
        for entry in source["plugin"]
    }
    listed = {
        entry["name"]: [(release["version"], release["url"]) for release in entry["releases"]]
        for entry in index["plugins"]
    }
    assert listed == wanted
    assert list(listed) == ["postulo-helloworld"], "for now, only the reference plugin"


def test_every_release_it_lists_fits_this_postulo_and_is_a_plain_wheel():
    """A listing nobody can install would be a page full of buttons that refuse."""
    for listing in catalogue.parse((DIRECTORY / "index.json").read_bytes(), catalogue="official"):
        assert listing.latest is not None
        release = listing.latest
        assert installing.fits(release.requires_postulo), f"{listing.name} does not fit"
        assert re.fullmatch(r"[0-9a-f]{64}", release.sha256)
        assert release.url.startswith("https://source.tiagoagueda.com/postulo/")
        assert release.url.endswith("-py3-none-any.whl")


def test_the_key_instances_trust_is_the_key_that_signs():
    assert provenance.OFFICIAL_KEYS == (provenance.OFFICIAL_KEY,)
    catalogue.decode_public_key(provenance.OFFICIAL_KEY)


# --------------------------------------------------------------------- the row


def test_the_official_row_knows_where_the_catalogue_is_and_is_off(official):
    assert official.url == provenance.OFFICIAL_URL
    assert official.public_key == provenance.OFFICIAL_KEY
    assert not official.enabled, "Postulo asks for nothing until an administrator says so"
    assert "official" not in catalogue.configured()


def test_switching_it_on_offers_it_and_off_withdraws_it(official):
    official.enabled = True
    official.save()
    assert catalogue.configured()["official"] == {
        "url": provenance.OFFICIAL_URL,
        "key": provenance.OFFICIAL_KEY,
    }

    official.enabled = False
    official.save()
    assert "official" not in catalogue.configured()


def test_the_administrator_switches_it_on_and_off_from_the_page(client, admin, official):
    client.force_login(admin)

    page = client.get(reverse("server:plugins")).content.decode()
    row = re.search(r'<li[^>]*data-repository="official".*?</li>', page, re.S).group(0)
    assert "Disabled" in row and "Switch on" in row
    assert provenance.OFFICIAL_URL[:60] in row, "the page says where it would fetch from"

    client.post(reverse("server:plugin_repository"), {"name": "official", "action": "enable"})
    official.refresh_from_db()
    assert official.enabled
    page = client.get(reverse("server:plugins")).content.decode()
    row = re.search(r'<li[^>]*data-repository="official".*?</li>', page, re.S).group(0)
    assert "Switch off" in row and "Disabled" not in row

    client.post(reverse("server:plugin_repository"), {"name": "official", "action": "disable"})
    official.refresh_from_db()
    assert not official.enabled


def test_an_upgrade_does_not_overwrite_a_row_somebody_filled_in(official):
    import importlib

    migration = importlib.import_module(
        "postulo.plugins.migrations.0009_official_repository_address"
    )
    official.url, official.public_key, official.enabled = "https://mine.test/i.json", "k", True
    official.save()

    class Apps:
        @staticmethod
        def get_model(app, model):
            return PluginRepository

    migration.point(Apps, None)

    official.refresh_from_db()
    assert official.url == "https://mine.test/i.json" and official.public_key == "k"
    assert official.enabled


# -------------------------------------------------------- what it is for


def test_a_switched_on_official_repository_lists_helloworld_and_vouches_for_it(official, published):
    official.enabled = True
    official.save()

    fetched = catalogue.fetch("official")

    assert [listing.name for listing in fetched.listings] == ["postulo-helloworld"]
    release = fetched.listings[0].latest
    assert release.version == "0.3.0"
    assert provenance.official_repositories() == {"official"}

    entry = installing.Installed(
        name="postulo-helloworld",
        version="0.3.0",
        origin="upload",
        sha256=release.sha256,
    )
    assert provenance.of_record(entry).kind == provenance.OFFICIAL, (
        "the checksum it signed is what makes a file official, however it arrived"
    )


def test_switched_off_it_vouches_for_nothing(official):
    assert provenance.official_repositories() == set()
