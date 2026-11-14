# ruff: noqa: F811
"""One manifest, and nothing installed outside it (#1263).

Every way a plugin can arrive is tried with the development switch off, which is how an
instance runs: only a wheel a signed catalogue lists, with the checksum its verified index
carries, is installed. The test settings turn the switch on so that the install tests can
build wheels of their own, and `strict` turns it back off here.
"""

from __future__ import annotations

import base64
import datetime as dt
import importlib.util
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.urls import reverse

from postulo.plugins import catalogue, installing
from postulo.plugins.installing import InstallError

from .plugin_install_support import environment_read_once  # noqa: F401 - for every test here
from .test_plugin_install import (  # noqa: F401 - fixtures and helpers shared with that file
    a_catalogue,
    a_wheel,
    admin,
    configure,
    installer,
    plugins_dir,
    served,
)

pytestmark = pytest.mark.django_db

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def strict(settings):
    settings.POSTULO_PLUGIN_ALLOW_UNLISTED = False


def nothing_installed() -> bool:
    return installing.read_record() == []


# ------------------------------------------------- nothing installs outside a manifest


def test_a_wheel_nobody_listed_is_not_installed_by_the_installer(
    strict, tmp_path, plugins_dir, installer
):
    """The installer itself refuses a bare wheel, whichever route called it (#1263)."""
    wheel = a_wheel(tmp_path)
    with pytest.raises(InstallError, match="signed catalogue"):
        installing.install_wheel(wheel)
    assert nothing_installed() and installer == []


def test_a_catalogue_origin_without_a_checksum_is_not_a_listing(
    strict, tmp_path, plugins_dir, installer
):
    """Claiming a catalogue is not proof: the verified index's checksum has to come with it."""
    wheel = a_wheel(tmp_path)
    with pytest.raises(InstallError):
        installing.install_wheel(wheel, origin="catalogue:official")
    with pytest.raises(InstallError):
        installing.install_wheel(
            wheel, origin="upload", expected_sha256=installing.digest_of(wheel)
        )
    assert nothing_installed()


def test_the_upload_route_installs_nothing_when_the_switch_is_off(
    strict, client, admin, tmp_path, plugins_dir, installer
):
    client.force_login(admin)
    wheel = a_wheel(tmp_path)
    response = client.post(
        reverse("server:plugin_action"),
        {
            "action": "upload",
            "package": SimpleUploadedFile("p.whl", wheel.read_bytes()),
        },
        follow=True,
    )
    assert "signed catalogue only" in response.content.decode()
    assert "plugin_pending" not in client.session
    assert nothing_installed() and not (plugins_dir / ".pending").exists()


def test_confirming_a_package_installs_nothing_when_the_switch_is_off(
    strict, client, admin, tmp_path, plugins_dir, installer
):
    """A session that already holds a waiting package (made while the switch was on) is refused."""
    client.force_login(admin)
    pending = plugins_dir / ".pending"
    pending.mkdir(parents=True)
    wheel = a_wheel(tmp_path)
    (pending / "tok.whl").write_bytes(wheel.read_bytes())
    session = client.session
    session["plugin_pending"] = {"token": "tok", "name": "postulo-example", "filename": "p.whl"}
    session.save()
    client.post(reverse("server:plugin_action"), {"action": "confirm", "token": "tok"})
    assert nothing_installed() and installer == []


def test_the_command_refuses_a_path_and_a_url_when_the_switch_is_off(
    strict, tmp_path, plugins_dir, installer
):
    wheel = a_wheel(tmp_path)
    with pytest.raises(CommandError, match="switched off"):
        call_command("plugins", "install", str(wheel))
    # A bare address is not a catalogue name: no catalogue lists it.
    with pytest.raises(CommandError):
        call_command("plugins", "install", "https://example.org/x-1.0-py3-none-any.whl")
    assert nothing_installed() and installer == []


def test_the_page_offers_no_upload_when_the_switch_is_off(strict, client, admin, plugins_dir):
    client.force_login(admin)
    html = client.get(reverse("server:plugins")).content.decode()
    assert 'name="package"' not in html


def test_the_page_offers_an_upload_with_a_warning_when_the_switch_is_on(
    settings, client, admin, plugins_dir
):
    settings.POSTULO_PLUGIN_ALLOW_UNLISTED = True
    client.force_login(admin)
    html = client.get(reverse("server:plugins")).content.decode()
    assert 'name="package"' in html and "data-unlisted-warning" in html


def test_every_action_the_page_posts_is_a_catalogue_or_a_local_one(strict, client, admin):
    """No action takes an address or a package name from the form to fetch from."""
    from postulo.core.server_views import PluginActionView

    assert set(PluginActionView.ACTIONS) == {
        "upload", "cancel", "confirm", "refresh", "install", "disable", "enable", "remove",
    }  # fmt: skip
    client.force_login(admin)
    for field in ("url", "source", "wheel", "pip", "index"):
        client.post(
            reverse("server:plugin_action"),
            {"action": "install", "name": "x", field: "https://example.org/x.whl"},
        )
    assert nothing_installed()


def test_only_the_catalogue_and_the_restore_install_a_wheel():
    """The call sites of `install_wheel`, so a new route has to be added here on purpose."""
    found = set()
    for path in (ROOT / "src").rglob("*.py"):
        if "install_wheel(" in path.read_text(encoding="utf-8"):
            found.add(path.relative_to(ROOT / "src" / "postulo").as_posix())
    assert found == {
        "plugins/installing.py",  # the definition, and `sync`'s restore
        "plugins/catalogue.py",  # a verified release
        "core/server_views.py",  # the upload, behind the development switch
        "plugins/management/commands/plugins.py",  # a path, behind the development switch
    }


def test_a_catalogue_install_still_works_with_the_switch_off(
    strict, served, settings, plugins_dir, installer
):
    configure(settings, served["public"])
    entry = catalogue.install("postulo-example", by="ana")
    assert entry.origin == "catalogue:official"


def test_a_restore_puts_back_only_what_a_catalogue_installed(
    strict, served, settings, plugins_dir, installer
):
    """`sync` re-installs from the catalogue's checksum; an unlisted entry is not restored."""
    configure(settings, served["public"])
    catalogue.install("postulo-example")
    entry = installing.read_record()[0]
    assert entry.origin.startswith("catalogue:") and entry.sha256
    entry.origin = "upload"
    installing.write_record([entry])
    wheel = served["wheel_path"]
    for path in plugins_dir.glob("postulo_example*"):
        if path.is_dir():
            import shutil

            shutil.rmtree(path)
    restored, lost = installing.sync(fetch=lambda _entry: wheel)
    assert restored == [] and lost == ["postulo-example"]


# ------------------------------------------------- a signed index, or no catalogue


def test_an_index_signed_by_another_key_is_refused_with_a_message(
    served, settings, client, admin, plugins_dir
):
    other = base64.b64encode(Ed25519PrivateKey.generate().public_key().public_bytes_raw()).decode()
    configure(settings, other)
    with pytest.raises(catalogue.CatalogueError, match="signature does not match"):
        catalogue.fetch("official")
    client.force_login(admin)
    response = client.post(reverse("server:plugin_action"), {"action": "refresh"}, follow=True)
    assert "signature does not match" in response.content.decode()


def test_an_unsigned_index_is_refused(served, settings):
    configure(settings, served["public"])
    served["signature"] = ""
    with pytest.raises(catalogue.CatalogueError):
        catalogue.fetch("official")


def test_a_repository_added_without_a_key_is_never_offered(client, admin):
    """It may be saved as a draft, but with no key it is not a catalogue: nothing is fetched."""
    client.force_login(admin)
    client.post(
        reverse("server:plugin_repository"),
        {
            "action": "add",
            "name": "mine",
            "url": "https://example.org/index.json",
            "public_key": "",
        },
    )
    assert "mine" not in catalogue.configured()


# ----------------------------------------------------------------- one list, origins


def test_a_listing_names_its_origin_official_by_key_never_by_name(
    served, settings, client, admin, plugins_dir
):
    """A catalogue called "official" with an unknown key is third party."""
    configure(settings, served["public"])
    client.force_login(admin)
    client.post(reverse("server:plugin_action"), {"action": "refresh"})
    html = client.get(reverse("server:plugins")).content.decode()
    assert 'data-origin="third-party"' in html and 'data-origin="official"' not in html

    from postulo.plugins import provenance

    monkey = pytest.MonkeyPatch()
    monkey.setattr(provenance, "OFFICIAL_KEYS", (served["public"],))
    try:
        client.post(reverse("server:plugin_action"), {"action": "refresh"})
        html = client.get(reverse("server:plugins")).content.decode()
    finally:
        monkey.undo()
    assert 'data-origin="official"' in html


def test_a_built_in_row_shows_its_version_and_has_nothing_to_press(client, admin, plugins_dir):
    client.force_login(admin)
    from postulo.plugins import provenance

    built_in = next(row for row in provenance.status() if row["provenance"] == "internal")
    html = client.get(reverse("server:plugins")).content.decode()
    start = html.index(f'data-plugin="{built_in["name"]}"')
    row = html[start : html.index("</li>", start)]
    assert built_in["version"] in row
    assert 'value="remove"' not in row and 'value="install"' not in row


# ----------------------------------------------------------------------- expiry


def expired_index(settings, served, *, days: int) -> None:
    """The served catalogue, re-signed with an expiry `days` from now."""
    document = json.loads(served["index"])
    document["expires"] = (dt.datetime.now(dt.UTC) + dt.timedelta(days=days)).isoformat()
    payload = json.dumps(document).encode()
    key = Ed25519PrivateKey.generate()
    served["index"] = payload
    served["signature"] = base64.b64encode(key.sign(payload)).decode()
    configure(settings, base64.b64encode(key.public_key().public_bytes_raw()).decode())


def test_an_expired_index_says_so_and_why(served, settings, client, admin, plugins_dir):
    expired_index(settings, served, days=-1)
    with pytest.raises(catalogue.CatalogueExpired, match="cannot be used"):
        catalogue.fetch("official")
    client.force_login(admin)
    response = client.post(reverse("server:plugin_action"), {"action": "refresh"}, follow=True)
    text = response.content.decode()
    assert "expired on" in text and "cannot be used" in text
    assert "already installed go on working" in text


def tool():
    spec = importlib.util.spec_from_file_location(
        "official_catalogue", ROOT / "scripts" / "official_catalogue.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_committed_index_has_more_than_thirty_days_left():
    """Fails in CI once the official index is within 30 days of expiry, while there is time to
    sign it again (#1263). The key is held by a person; nothing here can do it for them."""
    assert tool().expiring(tool().EXPIRY_WARNING_DAYS) == ""


def test_the_expiry_check_fails_close_to_expiry_and_after():
    module = tool()
    expires = dt.datetime.fromisoformat(
        json.loads(module.INDEX.read_bytes())["expires"].replace("Z", "+00:00")
    )
    assert "expires in 29 days" in module.expiring(30, now=expires - dt.timedelta(days=29, hours=1))
    assert "has expired" in module.expiring(30, now=expires + dt.timedelta(days=1))
    assert module.expiring(30, now=expires - dt.timedelta(days=45)) == ""
