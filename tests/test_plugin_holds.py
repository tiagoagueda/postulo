# ruff: noqa: F811
"""Holding a plugin where it is, or pinning it to a version (#1264).

The installer is stood in for as in `test_plugin_install`; the wheels, the signed index, the
record and the checks around them are real.
"""

from __future__ import annotations

import base64
import json
import logging

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from django.core.management import call_command
from django.urls import reverse

from postulo.plugins import catalogue, holds, installing
from postulo.plugins.installing import InstallError
from postulo.plugins.models import PluginHold
from postulo.plugins.record import Installed

from .plugin_install_support import environment_read_once  # noqa: F401 - for every test here
from .test_plugin_install import a_wheel, admin, installer, plugins_dir  # noqa: F401

pytestmark = pytest.mark.django_db

NAME = "postulo-example"


@pytest.fixture
def releases(monkeypatch, tmp_path, settings):
    """One signed catalogue listing three releases of one plugin, 3.0 flagged a security fix."""
    wheels, entries = {}, []
    for version, extra in (
        ("3.0", {"security": True, "advisory": "https://example.org/advisory/3"}),
        ("2.0", {}),
        ("1.0", {}),
        ("0.9", {"requires_postulo": ">=99"}),
    ):
        folder = tmp_path / version
        folder.mkdir()
        wheel = a_wheel(folder, version=version)
        wheels[wheel.name] = wheel.read_bytes()
        entries.append(
            {
                "version": version,
                "url": f"https://plugins.example.org/{wheel.name}",
                "sha256": installing.digest_of(wheel),
                "requires_postulo": ">=0.2",
                "provides": ["postulo.sources:example"],
                **extra,
            }
        )
    index = json.dumps({"plugins": [{"name": NAME, "summary": "x", "releases": entries}]}).encode()
    key = Ed25519PrivateKey.generate()
    state = {
        "index": index,
        "signature": base64.b64encode(key.sign(index)).decode(),
        "wheels": wheels,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/index.json"):
            return httpx.Response(200, content=state["index"])
        if path.endswith("/index.json.sig"):
            return httpx.Response(200, content=state["signature"].encode())
        body = state["wheels"].get(path.rsplit("/", 1)[-1])
        return httpx.Response(200, content=body) if body else httpx.Response(404)

    def client(**kwargs):
        kwargs.pop("event_hooks", None)
        kwargs.setdefault("timeout", 10)
        return httpx.Client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(catalogue.http, "client", client)
    public = base64.b64encode(key.public_key().public_bytes_raw()).decode()
    settings.POSTULO_PLUGIN_CATALOGUES = f"main|https://plugins.example.org/index.json|{public}"
    return state


def version_installed() -> str | None:
    entry = installing.installed(NAME)
    return entry.version if entry else None


def post(client, **fields):
    return client.post(reverse("server:plugin_action"), fields, follow=True)


# ------------------------------------------------------------------- holding


def test_a_hold_keeps_the_installed_version_and_says_who_and_why(
    releases, plugins_dir, installer, admin, caplog
):
    catalogue.install(NAME, version="2.0")
    with caplog.at_level(logging.WARNING, logger="postulo.plugins.audit"):
        held = holds.hold(NAME, by=admin, note="2.0 is the one we reviewed")

    assert (held.mode, held.version, held.set_by) == ("hold", "2.0", admin.get_username())
    row = PluginHold.objects.get()
    assert row.set_by == admin and row.note == "2.0 is the one we reviewed"
    # The audit trail: the instance log, with the administrator's name.
    assert any(
        "held" in record.getMessage() and admin.get_username() in record.getMessage()
        for record in caplog.records
    )


def test_a_hold_survives_a_restart_and_is_read_from_the_database(
    releases, plugins_dir, installer, admin
):
    """Nothing about a hold is kept in the process: a fresh process finds the row."""
    catalogue.install(NAME, version="2.0")
    holds.hold(NAME, by=admin)
    assert holds.is_held(NAME) and holds.get(NAME).version == "2.0"
    assert PluginHold.objects.filter(plugin=NAME).exists()


def test_what_is_not_installed_cannot_be_held(releases, plugins_dir, installer, admin):
    with pytest.raises(holds.HoldError, match="not installed"):
        holds.hold(NAME, by=admin)


def test_lifting_a_hold_follows_the_latest_again_and_is_logged(
    releases, plugins_dir, installer, admin, caplog
):
    catalogue.install(NAME, version="2.0")
    holds.hold(NAME, by=admin)
    with caplog.at_level(logging.WARNING, logger="postulo.plugins.audit"):
        holds.lift(NAME, by=admin)
    assert not holds.is_held(NAME)
    assert any("lifted" in record.getMessage() for record in caplog.records)


def test_a_held_plugin_is_skipped_by_automatic_updates_and_still_shown_with_its_newer_release(
    releases, plugins_dir, installer, admin, client
):
    """The predicate every automatic or bulk update asks; the page still lists 3.0, as held."""
    catalogue.install(NAME, version="2.0")
    holds.hold(NAME, by=admin)
    assert holds.is_held(NAME)

    client.force_login(admin)
    client.post(reverse("server:plugin_action"), {"action": "refresh"})
    html = client.get(reverse("server:plugins")).content.decode()
    assert f"{NAME} 3.0" in html and 'data-hold="hold"' in html
    assert version_installed() == "2.0"


def test_moving_a_held_plugin_by_hand_asks_first_and_the_hold_follows(
    releases, plugins_dir, installer, admin, client
):
    catalogue.install(NAME, version="2.0")
    holds.hold(NAME, by=admin)
    client.force_login(admin)

    first = post(client, action="install", name=NAME, catalogue="main", version="3.0")
    assert "is held at 2.0" in first.content.decode()
    assert version_installed() == "2.0"

    post(client, action="install", name=NAME, catalogue="main", version="3.0", confirm_held="1")
    assert version_installed() == "3.0"
    assert holds.get(NAME).version == "3.0" and holds.get(NAME).mode == "hold"


def test_a_security_release_is_flagged_for_a_held_plugin_and_not_applied(
    releases, plugins_dir, installer, admin, client
):
    catalogue.install(NAME, version="2.0")
    holds.hold(NAME, by=admin)
    client.force_login(admin)
    client.post(reverse("server:plugin_action"), {"action": "refresh"})
    html = client.get(reverse("server:plugins")).content.decode()
    assert f'data-security-held="{NAME}"' in html
    assert "https://example.org/advisory/3" in html
    assert version_installed() == "2.0"


def test_a_plugin_nothing_holds_says_nothing_about_security_holds(
    releases, plugins_dir, installer, admin, client
):
    catalogue.install(NAME, version="2.0")
    client.force_login(admin)
    client.post(reverse("server:plugin_action"), {"action": "refresh"})
    assert "data-security-held" not in client.get(reverse("server:plugins")).content.decode()


# -------------------------------------------------------------------- pinning


def test_a_pin_installs_exactly_that_version_down_and_up(
    releases, plugins_dir, installer, admin, client
):
    client.force_login(admin)
    catalogue.install(NAME, version="2.0")

    post(client, action="pin", name=NAME, catalogue="main", version="1.0", note="3.0 broke us")
    assert version_installed() == "1.0"
    pinned = holds.get(NAME)
    assert (pinned.mode, pinned.version, pinned.note) == ("pin", "1.0", "3.0 broke us")

    post(client, action="pin", name=NAME, catalogue="main", version="3.0")
    assert version_installed() == "3.0" and holds.get(NAME).version == "3.0"


def test_a_pin_that_fails_to_install_is_rolled_back_and_not_recorded(
    releases, plugins_dir, installer, admin, monkeypatch
):
    catalogue.install(NAME, version="2.0")

    def broken(target, wheel, constraint_file):
        raise InstallError("pip fell over")

    monkeypatch.setattr(installing, "run_install", broken)
    with pytest.raises(InstallError, match="pip fell over"):
        catalogue.pin(NAME, "1.0", by=admin, catalogue="main")
    assert version_installed() == "2.0" and not PluginHold.objects.exists()


def test_a_pin_for_a_postulo_this_is_not_is_refused_with_the_reason(
    releases, plugins_dir, installer, admin
):
    catalogue.install(NAME, version="2.0")
    with pytest.raises(catalogue.CatalogueError, match="is for Postulo >=99"):
        catalogue.pin(NAME, "0.9", by=admin, catalogue="main")
    assert version_installed() == "2.0" and not PluginHold.objects.exists()


def test_a_pin_is_refused_when_another_plugin_does_not_accept_that_version(
    releases, plugins_dir, installer, admin
):
    catalogue.install(NAME, version="2.0")
    entries = installing.read_record()
    installing.write_record(
        [*entries, Installed(name="postulo-other", version="1.0", requires=[f"{NAME}>=2"])]
    )
    with pytest.raises(catalogue.CatalogueError, match="postulo-other needs postulo-example>=2"):
        catalogue.pin(NAME, "1.0", by=admin, catalogue="main")
    assert version_installed() == "2.0"
    # A version the dependant accepts is fine.
    catalogue.pin(NAME, "3.0", by=admin, catalogue="main")
    assert version_installed() == "3.0"


# ------------------------------------------------------------ the environment


def test_an_environment_pin_wins_and_cannot_be_changed_from_the_page(
    releases, plugins_dir, installer, admin, client, settings
):
    catalogue.install(NAME, version="2.0")
    settings.POSTULO_PLUGIN_PINS = f"{NAME}==2.0, nonsense, other=="
    assert holds.environment() == {NAME: "2.0"}
    assert holds.get(NAME).from_environment and holds.is_held(NAME)

    client.force_login(admin)
    for action, extra in (
        ("lift", {}),
        ("hold", {}),
        ("pin", {"version": "1.0", "catalogue": "main"}),
        ("install", {"version": "3.0", "catalogue": "main", "confirm_held": "1"}),
    ):
        text = post(client, action=action, name=NAME, **extra).content.decode()
        assert "POSTULO_PLUGIN_PINS" in text, action
    assert version_installed() == "2.0" and not PluginHold.objects.exists()

    html = client.get(reverse("server:plugins")).content.decode()
    assert "Pinned by POSTULO_PLUGIN_PINS" in html
    assert 'value="lift"' not in html and 'value="hold"' not in html


# ---------------------------------------------------------- Postulo and the command


def test_a_held_plugin_that_does_not_fit_the_next_postulo_is_named(
    releases, plugins_dir, installer, admin
):
    catalogue.install(NAME, version="2.0")
    holds.hold(NAME, by=admin)
    assert holds.incompatible_with("0.5.0") == []
    assert holds.incompatible_with("0.1.0") == [(NAME, "2.0", ">=0.2")]


def test_the_command_holds_lifts_and_lists(releases, plugins_dir, installer, capsys):
    catalogue.install(NAME, version="2.0")
    call_command("plugins", "hold", NAME, "--note", "reviewed")
    call_command("plugins", "holds", "--postulo", "0.1.0")
    out = capsys.readouterr().out
    assert "held at 2.0" in out and "reviewed" in out and "does not include 0.1.0" in out
    call_command("plugins", "lift", NAME)
    assert not holds.is_held(NAME)


def test_a_catalogue_release_signed_as_a_security_fix_is_read_as_one(releases):
    listing = catalogue.fetch("main").listings[0]
    secure = holds.security_release(listing, "2.0")
    assert secure.version == "3.0" and secure.advisory == "https://example.org/advisory/3"
    assert holds.security_release(listing, "3.0") is None
