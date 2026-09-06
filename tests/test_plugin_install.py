"""Installing plugins: the directory on the volume, the record, uploads, catalogues.

The installer itself is never run here — shelling out to pip in a test would be slow and
would reach the network — so `run_install` is replaced by something that lays down the
files pip would have laid down. Everything around it is real: the wheels are real wheels,
built in the test; the metadata is read out of them; the record is written and read; the
catalogue's signature is a real Ed25519 signature over the bytes that are checked.
"""

from __future__ import annotations

import base64
import importlib
import importlib.metadata
import json
import shutil
import sys
import zipfile
from pathlib import Path

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from django.core.management import call_command
from django.core.management.base import CommandError
from django.urls import reverse

from postulo.plugins import catalogue, installing
from postulo.plugins.installing import InstallError

pytestmark = pytest.mark.django_db


def a_wheel(
    path: Path,
    *,
    name="postulo-example",
    version="1.0",
    entry_points=("[postulo.sources]\nexample = postulo_example:Source\n",),
    requires=(),
    tag="py3-none-any",
    summary="An example plugin",
    licence="MIT",
) -> Path:
    """A real wheel, with the three files that matter and one module."""
    wheel = path / f"{name.replace('-', '_')}-{version}-{tag}.whl"
    dist_info = f"{name.replace('-', '_')}-{version}.dist-info"
    metadata = [
        "Metadata-Version: 2.1",
        f"Name: {name}",
        f"Version: {version}",
        f"Summary: {summary}",
        f"License: {licence}",
        "Author: A Person",
        "Requires-Python: >=3.12",
    ]
    metadata += [f"Requires-Dist: {requirement}" for requirement in requires]
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(f"{dist_info}/METADATA", "\n".join(metadata) + "\n\n")
        archive.writestr(f"{dist_info}/WHEEL", f"Wheel-Version: 1.0\nTag: {tag}\n")
        for text in entry_points:
            archive.writestr(f"{dist_info}/entry_points.txt", text)
        archive.writestr(
            f"{name.replace('-', '_')}/__init__.py",
            "class Source:\n"
            "    name = 'example'\n"
            f"    version = '{version}'\n"
            "    def can_handle(self, url):\n"
            "        return 'example.test' in url\n"
            "    def parse(self, url, html):\n"
            "        return None\n",
        )
    return wheel


@pytest.fixture
def plugins_dir(tmp_path, settings):
    """This test's own plugins directory, taken off the import path again afterwards.

    Installing puts the directory on ``sys.path``; leaving it there would let one test's
    packages be found by the next, which is exactly the confusion these tests rule out.
    """
    directory = tmp_path / "plugins"
    settings.POSTULO_PLUGINS_DIR = directory
    yield directory
    while str(directory) in sys.path:
        sys.path.remove(str(directory))
    importlib.invalidate_caches()
    importlib.metadata.MetadataPathFinder.invalidate_caches()


@pytest.fixture
def installer(monkeypatch, plugins_dir):
    """Stand in for pip: unpack the wheel where pip would have put it, and record the call."""
    calls: list[dict] = []

    def fake(target: Path, wheel: Path, constraint_file: Path) -> str:
        calls.append(
            {
                "target": Path(target),
                "wheel": Path(wheel),
                "constraints": constraint_file.read_text(encoding="utf-8"),
            }
        )
        with zipfile.ZipFile(wheel) as archive:
            archive.extractall(target)
        for dist_info in Path(target).glob("*.dist-info"):
            (dist_info / "RECORD").write_text(
                "\n".join(
                    f"{member},," for member in _members(wheel) if not member.endswith("RECORD")
                )
                + "\n",
                encoding="utf-8",
            )
        return "installed"

    monkeypatch.setattr(installing, "run_install", fake)
    return calls


def _members(wheel: Path) -> list[str]:
    with zipfile.ZipFile(wheel) as archive:
        return archive.namelist()


@pytest.fixture
def admin(user):
    user.is_staff = True
    user.is_superuser = True
    user.save()
    return user


# ------------------------------------------------------------ reading a wheel


def test_a_wheel_is_read_before_anything_is_installed(tmp_path, plugins_dir):
    wheel = a_wheel(tmp_path, requires=("httpx>=0.28", "tzdata; extra == 'zones'"))
    info = installing.read_wheel(wheel)
    assert info.name == "postulo-example" and info.version == "1.0"
    assert info.summary == "An example plugin" and info.licence == "MIT"
    assert info.entry_points == ["postulo.sources:example"]
    assert info.requires == ["httpx>=0.28"], "an extra's requirement is not this install's"
    assert info.pure_python and info.is_plugin
    assert info.sha256 == installing.digest_of(wheel)
    assert not plugins_dir.exists(), "reading installs nothing"


def test_something_that_is_not_a_wheel_says_so(tmp_path):
    junk = tmp_path / "notes.txt"
    junk.write_text("hello")
    with pytest.raises(InstallError, match="not a wheel"):
        installing.read_wheel(junk)


def test_a_wheel_for_one_platform_only_is_refused(tmp_path):
    wheel = a_wheel(tmp_path, tag="cp314-cp314-manylinux_2_17_x86_64")
    with pytest.raises(InstallError, match="not pure Python"):
        installing.check(installing.read_wheel(wheel))


def test_a_package_that_is_not_a_plugin_is_refused(tmp_path):
    wheel = a_wheel(tmp_path, entry_points=("[console_scripts]\nx = y:z\n",))
    with pytest.raises(InstallError, match="no Postulo entry point"):
        installing.check(installing.read_wheel(wheel))


def test_a_dependency_that_would_move_one_of_postulos_own_is_refused(tmp_path):
    wheel = a_wheel(tmp_path, requires=("django==4.2",))
    with pytest.raises(InstallError, match="would change what Postulo itself depends on"):
        installing.check(installing.read_wheel(wheel))
    assert any(pin.startswith("django==") for pin in installing.constraints())


# ------------------------------------------------------------- installing


def test_installing_puts_it_on_the_volume_and_records_it(tmp_path, plugins_dir, installer):
    wheel = a_wheel(tmp_path)
    entry = installing.install_wheel(wheel, by="ana")

    assert entry.name == "postulo-example" and entry.version == "1.0"
    assert entry.origin == "upload" and entry.installed_by == "ana"
    assert entry.entry_points == ["postulo.sources:example"]
    assert (plugins_dir / "postulo_example" / "__init__.py").is_file()
    assert installing.record_path().is_file()
    assert [item.name for item in installing.read_record()] == ["postulo-example"]
    assert installing.installed("Postulo_Example") is not None, "names match loosely"

    written = json.loads(installing.record_path().read_text(encoding="utf-8"))
    assert written["plugins"][0]["sha256"] == installing.digest_of(wheel)

    # The installer was given the running environment as a constraint, and the file went.
    assert "django==" in installer[0]["constraints"]
    assert not (plugins_dir / ".constraints.txt").exists()

    assert str(plugins_dir) in sys.path, "and it is importable at once"


def test_installing_again_replaces_the_line_rather_than_adding_one(
    tmp_path, plugins_dir, installer
):
    installing.install_wheel(a_wheel(tmp_path))
    installing.install_wheel(a_wheel(tmp_path, version="1.1"))
    record = installing.read_record()
    assert len(record) == 1 and record[0].version == "1.1"


def test_a_checksum_that_does_not_match_stops_the_install(tmp_path, plugins_dir, installer):
    wheel = a_wheel(tmp_path)
    with pytest.raises(InstallError, match="does not match the checksum"):
        installing.install_wheel(wheel, expected_sha256="0" * 64)
    assert installing.read_record() == []


def test_an_installer_that_fails_says_what_it_said(tmp_path, plugins_dir, monkeypatch):
    def refuse(target, wheel, constraint_file):
        raise InstallError("The installer refused it: no matching distribution")

    monkeypatch.setattr(installing, "run_install", refuse)
    with pytest.raises(InstallError, match="no matching distribution"):
        installing.install_wheel(a_wheel(tmp_path))
    assert installing.read_record() == []


# --------------------------------------------------------- switching off


def test_a_plugin_can_be_switched_off_without_being_removed(tmp_path, plugins_dir, installer):
    installing.install_wheel(a_wheel(tmp_path))
    installing.set_disabled("postulo-example", True)
    assert installing.disabled_names() == {"postulo-example"}
    assert (plugins_dir / "postulo_example" / "__init__.py").is_file(), "the files stay"
    installing.set_disabled("postulo-example", False)
    assert installing.disabled_names() == set()
    with pytest.raises(InstallError, match="not installed"):
        installing.set_disabled("nothing-like-it", True)


def test_the_registry_leaves_out_what_is_switched_off(
    tmp_path, plugins_dir, installer, monkeypatch
):
    from postulo.plugins import registry

    class Fake:
        name = "example"
        dist = type("D", (), {"name": "postulo-example"})()
        module = "postulo_example"

        def load(self):
            class Source:
                name = "example"
                version = "1.0"

                def can_handle(self, url):
                    return False

                def parse(self, url, html):
                    return None

            return Source

    monkeypatch.setattr(
        registry, "entry_points", lambda group: [Fake()] if group == "postulo.sources" else []
    )
    monkeypatch.setattr(registry, "register_plugin_locale", lambda module: None)
    installing.install_wheel(a_wheel(tmp_path))
    assert "example" in [source.name for source in registry.available_sources(refresh=True)]

    installing.set_disabled("postulo-example", True)
    assert "example" not in [source.name for source in registry.available_sources(refresh=True)]


# ------------------------------------------------------------- removing


def test_removing_takes_the_files_and_the_line(tmp_path, plugins_dir, installer):
    installing.install_wheel(a_wheel(tmp_path))
    entry = installing.remove("postulo-example")
    assert entry.version == "1.0"
    assert not (plugins_dir / "postulo_example").exists()
    assert not list(plugins_dir.glob("*.dist-info"))
    assert installing.read_record() == []
    with pytest.raises(InstallError, match="not installed"):
        installing.remove("postulo-example")


# ---------------------------------------------------------------- sync


def test_sync_reinstalls_what_the_record_lists_and_the_volume_lost(
    tmp_path, plugins_dir, installer
):
    wheel = a_wheel(tmp_path)
    installing.install_wheel(wheel)

    # An upgrade: a brand-new environment, the record intact, the files gone.
    for path in plugins_dir.iterdir():
        if path.name != installing.RECORD_NAME:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()

    restored, lost = installing.sync(fetch=lambda entry: wheel)
    assert restored == ["postulo-example"] and lost == []
    assert (plugins_dir / "postulo_example" / "__init__.py").is_file()

    restored, lost = installing.sync(fetch=lambda entry: wheel)
    assert restored == [] and lost == [], "nothing to do the second time"


def test_sync_says_what_it_could_not_bring_back(tmp_path, plugins_dir, installer):
    installing.install_wheel(a_wheel(tmp_path))
    shutil.rmtree(plugins_dir / "postulo_example")
    for dist_info in plugins_dir.glob("*.dist-info"):
        shutil.rmtree(dist_info)
    restored, lost = installing.sync(fetch=lambda entry: None)
    assert restored == [] and lost == ["postulo-example"]


# ------------------------------------------------------------ catalogues


def a_catalogue(wheel: Path, *, name="postulo-example", version="1.0") -> tuple[bytes, str, str]:
    """An index, its signature, and the public key to check it with."""
    index = json.dumps(
        {
            "plugins": [
                {
                    "name": name,
                    "summary": "An example plugin",
                    "maintainer": "A Person",
                    "licence": "MIT",
                    "repository": "https://example.org/example",
                    "releases": [
                        {
                            "version": version,
                            "url": f"https://plugins.example.org/{wheel.name}",
                            "sha256": installing.digest_of(wheel),
                            "requires_postulo": ">=0.2",
                            "provides": ["postulo.sources:example"],
                        }
                    ],
                }
            ]
        }
    ).encode()
    key = Ed25519PrivateKey.generate()
    signature = base64.b64encode(key.sign(index)).decode()
    public = base64.b64encode(key.public_key().public_bytes_raw()).decode()
    return index, signature, public


@pytest.fixture
def served(monkeypatch, tmp_path):
    """A catalogue on a web server that lives in memory."""
    wheel = a_wheel(tmp_path)
    index, signature, public = a_catalogue(wheel)
    state = {"index": index, "signature": signature, "wheel": wheel.read_bytes()}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/index.json"):
            return httpx.Response(200, content=state["index"])
        if path.endswith("/index.json.sig"):
            return httpx.Response(200, content=state["signature"].encode())
        if path.endswith(wheel.name):
            return httpx.Response(200, content=state["wheel"])
        return httpx.Response(404)

    def client(**kwargs):
        kwargs.pop("event_hooks", None)
        kwargs.setdefault("timeout", 10)
        return httpx.Client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(catalogue.http, "client", client)
    state["public"] = public
    state["wheel_path"] = wheel
    return state


def configure(settings, public: str) -> None:
    settings.POSTULO_PLUGIN_CATALOGUES = f"official|https://plugins.example.org/index.json|{public}"


def test_a_catalogue_is_read_only_when_its_signature_checks_out(served, settings):
    configure(settings, served["public"])
    assert sorted(catalogue.configured()) == ["official"]

    one = catalogue.fetch("official")
    assert [listing.name for listing in one.listings] == ["postulo-example"]
    assert one.listings[0].latest.version == "1.0"
    assert one.listings[0].licence == "MIT"

    served["index"] = served["index"].replace(b"An example plugin", b"Something else!!!")
    with pytest.raises(catalogue.CatalogueError, match="signature does not match"):
        catalogue.fetch("official")


def test_a_catalogue_nobody_configured_is_not_fetched(settings):
    settings.POSTULO_PLUGIN_CATALOGUES = ""
    assert catalogue.configured() == {}
    catalogues, problems = catalogue.fetch_all()
    assert catalogues == [] and problems == []
    with pytest.raises(catalogue.CatalogueError, match="No catalogue called"):
        catalogue.fetch("official")


def test_installing_from_a_catalogue_checks_the_wheel_against_the_signed_index(
    served, settings, plugins_dir, installer
):
    configure(settings, served["public"])
    entry = catalogue.install("postulo-example", by="ana")
    assert entry.origin == "catalogue:official"
    assert entry.source.endswith(".whl") and entry.installed_by == "ana"
    assert installing.installed("postulo-example") is not None

    # A wheel that is not the one the index vouched for is refused.
    other = a_wheel(Path(served["wheel_path"]).parent, version="9.9")
    served["wheel"] = other.read_bytes()
    with pytest.raises(catalogue.CatalogueError, match="does not match the checksum"):
        catalogue.install("postulo-example")


def test_a_plugin_no_catalogue_lists_cannot_be_installed_by_name(served, settings, plugins_dir):
    configure(settings, served["public"])
    with pytest.raises(catalogue.CatalogueError, match="No catalogue lists"):
        catalogue.install("something-else")


# ------------------------------------------------------------- the page


def test_the_page_shows_what_is_installed_and_warns_plainly(
    client, admin, plugins_dir, installer, tmp_path
):
    installing.install_wheel(a_wheel(tmp_path), by="ana")
    client.force_login(admin)
    html = client.get(reverse("server:plugins")).content.decode()
    assert "runs somebody else's code inside Postulo" in html
    assert "postulo-example" in html and "1.0" in html
    assert "postulo.sources:example" in html
    assert "No catalogue is configured" in html


def test_uploading_shows_the_package_before_installing_it(
    client, admin, plugins_dir, installer, tmp_path
):
    client.force_login(admin)
    wheel = a_wheel(tmp_path, requires=("httpx>=0.28",))
    with wheel.open("rb") as handle:
        client.post(
            reverse("server:plugin_action"),
            {"action": "upload", "package": handle},
        )
    html = client.get(reverse("server:plugins")).content.decode()
    assert "Nothing has been installed yet" in html
    assert "postulo-example" in html and "httpx&gt;=0.28" in html
    assert installing.read_record() == [], "reading is not installing"

    token = client.session["plugin_pending"]["token"]
    response = client.post(
        reverse("server:plugin_action"), {"action": "confirm", "token": token}, follow=True
    )
    assert "is installed" in response.content.decode()
    assert [item.name for item in installing.read_record()] == ["postulo-example"]
    assert "plugin_pending" not in client.session
    assert not (plugins_dir / ".pending").exists(), "the scratch copy goes"


def test_an_upload_that_is_refused_never_waits_for_confirmation(
    client, admin, plugins_dir, tmp_path
):
    client.force_login(admin)
    wheel = a_wheel(tmp_path, entry_points=("[console_scripts]\nx = y:z\n",))
    with wheel.open("rb") as handle:
        response = client.post(
            reverse("server:plugin_action"), {"action": "upload", "package": handle}, follow=True
        )
    assert "no Postulo entry point" in response.content.decode()
    assert "plugin_pending" not in client.session


def test_cancelling_leaves_nothing_behind(client, admin, plugins_dir, tmp_path):
    client.force_login(admin)
    with a_wheel(tmp_path).open("rb") as handle:
        client.post(reverse("server:plugin_action"), {"action": "upload", "package": handle})
    assert "plugin_pending" in client.session
    response = client.post(reverse("server:plugin_action"), {"action": "cancel"}, follow=True)
    assert "Nothing was installed" in response.content.decode()
    assert "plugin_pending" not in client.session
    assert not (plugins_dir / ".pending").exists()


def test_switching_off_and_removing_from_the_page(client, admin, plugins_dir, installer, tmp_path):
    installing.install_wheel(a_wheel(tmp_path))
    client.force_login(admin)

    response = client.post(
        reverse("server:plugin_action"),
        {"action": "disable", "name": "postulo-example"},
        follow=True,
    )
    assert "switched off" in response.content.decode()
    assert installing.disabled_names() == {"postulo-example"}

    client.post(reverse("server:plugin_action"), {"action": "enable", "name": "postulo-example"})
    assert installing.disabled_names() == set()

    response = client.post(
        reverse("server:plugin_action"),
        {"action": "remove", "name": "postulo-example"},
        follow=True,
    )
    assert "is removed" in response.content.decode()
    assert installing.read_record() == []


def test_the_catalogue_is_fetched_only_when_asked(
    client, admin, served, settings, plugins_dir, installer
):
    configure(settings, served["public"])
    client.force_login(admin)
    html = client.get(reverse("server:plugins")).content.decode()
    assert "Check for updates" in html and "postulo-example" not in html

    client.post(reverse("server:plugin_action"), {"action": "refresh"})
    html = client.get(reverse("server:plugins")).content.decode()
    assert "postulo-example" in html and "An example plugin" in html

    response = client.post(
        reverse("server:plugin_action"),
        {"action": "install", "name": "postulo-example"},
        follow=True,
    )
    assert "installed from the catalogue" in response.content.decode()
    assert installing.installed("postulo-example") is not None


def test_only_administrators_reach_any_of_it(client, user, plugins_dir):
    client.force_login(user)
    assert client.get(reverse("server:plugins")).status_code in (302, 403)
    response = client.post(reverse("server:plugin_action"), {"action": "remove", "name": "x"})
    assert response.status_code in (302, 403)
    assert reverse("server:plugins") not in response.headers.get("Location", "")


# ------------------------------------------------------------ the command


def test_the_command_lists_installs_and_removes(tmp_path, plugins_dir, installer, capsys):
    from io import StringIO

    out = StringIO()
    call_command("plugins", "list", stdout=out)
    assert "No plugins are installed" in out.getvalue()

    out = StringIO()
    call_command("plugins", "install", str(a_wheel(tmp_path)), stdout=out)
    assert "Installed postulo-example 1.0" in out.getvalue()
    assert "postulo.sources:example" in out.getvalue()

    out = StringIO()
    call_command("plugins", "list", stdout=out)
    assert "postulo-example 1.0  [upload]" in out.getvalue()

    out = StringIO()
    call_command("plugins", "disable", "postulo-example", stdout=out)
    assert "now disabled" in out.getvalue()
    out = StringIO()
    call_command("plugins", "list", stdout=out)
    assert "disabled" in out.getvalue()

    out = StringIO()
    call_command("plugins", "remove", "postulo-example", stdout=out)
    assert "Removed postulo-example" in out.getvalue()

    with pytest.raises(CommandError, match="not installed"):
        call_command("plugins", "remove", "postulo-example")
    with pytest.raises(CommandError, match="No such file"):
        call_command("plugins", "install", str(tmp_path / "nothing.whl"))


def test_the_command_syncs_and_reports_the_catalogue(tmp_path, plugins_dir, installer, settings):
    from io import StringIO

    out = StringIO()
    call_command("plugins", "sync", stdout=out)
    assert "Everything the record lists is present" in out.getvalue()

    settings.POSTULO_PLUGIN_CATALOGUES = ""
    out = StringIO()
    call_command("plugins", "catalogue", stdout=out)
    assert "No catalogue is configured" in out.getvalue()
