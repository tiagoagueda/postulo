# ruff: noqa: F811
"""Telling an administrator a plugin has an update, and applying one or all (#1187).

As in `test_plugin_install`, the installer is stood in for and everything around it is real:
wheels, the signed index, the record, the snapshot and the roll back.
"""

from __future__ import annotations

import base64
import datetime as dt
import json
import logging
import signal

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from django.urls import reverse
from django.utils import timezone

from postulo.core import scheduler
from postulo.plugins import catalogue, holds, installing, updates
from postulo.plugins.installing import InstallError
from postulo.plugins.models import CatalogueAnswer, PluginUpdateSettings

from .plugin_install_support import environment_read_once  # noqa: F401 - for every test here
from .test_plugin_install import a_wheel, admin, installer, plugins_dir  # noqa: F401

pytestmark = pytest.mark.django_db

A, B = "postulo-example", "postulo-other"


class Shop:
    """A signed catalogue whose contents a test changes, and a count of what was asked of it."""

    def __init__(self, monkeypatch, tmp_path, settings):
        self.tmp, self.key = tmp_path, Ed25519PrivateKey.generate()
        self.plugins: dict[str, list[dict]] = {}
        self.wheels: dict[str, bytes] = {}
        self.requests: list[str] = []
        self.expires = None
        self.tamper = False

        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            self.requests.append(path.rsplit("/", 1)[-1])
            if path.endswith("/index.json"):
                return httpx.Response(200, content=self.index())
            if path.endswith("/index.json.sig"):
                signature = base64.b64encode(self.key.sign(self.index(signed=True))).decode()
                return httpx.Response(200, content=signature.encode())
            body = self.wheels.get(path.rsplit("/", 1)[-1])
            return httpx.Response(200, content=body) if body else httpx.Response(404)

        def client(**kwargs):
            kwargs.pop("event_hooks", None)
            kwargs.setdefault("timeout", 10)
            return httpx.Client(transport=httpx.MockTransport(handler), **kwargs)

        monkeypatch.setattr(catalogue.http, "client", client)
        public = base64.b64encode(self.key.public_key().public_bytes_raw()).decode()
        settings.POSTULO_PLUGIN_CATALOGUES = f"main|https://plugins.example.org/index.json|{public}"

    def publish(self, name: str, version: str, **extra) -> None:
        folder = self.tmp / f"{name}-{version}"
        folder.mkdir(exist_ok=True)
        module = name.replace("-", "_")
        short = name.split("-")[-1]
        wheel = a_wheel(
            folder,
            name=name,
            version=version,
            entry_points=(f"[postulo.sources]\n{short} = {module}:Source\n",),
        )
        self.wheels[wheel.name] = wheel.read_bytes()
        release = {
            "version": version,
            "url": f"https://plugins.example.org/{wheel.name}",
            "sha256": installing.digest_of(wheel),
            "requires_postulo": ">=0.2",
            "provides": [f"postulo.sources:{short}"],
            **extra,
        }
        self.plugins.setdefault(name, []).insert(0, release)

    def index(self, signed: bool = False) -> bytes:
        document = {
            "plugins": [
                {"name": name, "summary": "x", "repository": f"https://example.org/{name}",
                 "releases": releases}
                for name, releases in self.plugins.items()
            ]
        }  # fmt: skip
        if self.expires is not None:
            document["expires"] = self.expires.isoformat()
        payload = json.dumps(document, sort_keys=True).encode()
        return payload

    def asked_for_a_wheel(self) -> bool:
        return any(name.endswith(".whl") for name in self.requests)


@pytest.fixture
def shop(monkeypatch, tmp_path, settings):
    return Shop(monkeypatch, tmp_path, settings)


def install(shop, name, version):
    return catalogue.install(name, version=version)


def version_of(name):
    entry = installing.installed(name)
    return entry.version if entry else None


def post(client, **fields):
    return client.post(reverse("server:plugin_action"), fields, follow=True)


# ------------------------------------------------------------------ knowing


def test_a_newer_compatible_release_is_an_update_compared_by_version_order(
    shop, plugins_dir, installer
):
    for version in ("1.0", "1.9", "1.10"):
        shop.publish(A, version)
    install(shop, A, "1.9")
    # 1.10 is later than 1.9 although "1.10" sorts before "1.9" as text.
    offers = updates.offers_from(catalogue.fetch("main"))
    assert [(o.plugin, o.installed, o.available) for o in offers] == [(A, "1.9", "1.10")]
    assert offers[0].compatible and not offers[0].breaking


def test_a_release_that_needs_another_postulo_is_shown_and_offers_no_button(
    shop, plugins_dir, installer, admin, client
):
    shop.publish(A, "1.0")
    shop.publish(A, "1.1", requires_postulo=">=99")
    install(shop, A, "1.0")
    offer = updates.offers_from(catalogue.fetch("main"))[0]
    assert offer.available == "1.1" and not offer.compatible

    updates.check()
    client.force_login(admin)
    html = client.get(reverse("server:plugins")).content.decode()
    assert "data-needs-postulo" in html and "Needs Postulo &gt;=99" in html
    assert 'value="update"' not in html and 'value="update_all"' in html


def test_a_newer_release_the_postulo_fits_wins_over_a_latest_that_does_not(
    shop, plugins_dir, installer
):
    shop.publish(A, "1.0")
    shop.publish(A, "1.1")
    shop.publish(A, "1.2", requires_postulo=">=99")
    install(shop, A, "1.0")
    offer = updates.offers_from(catalogue.fetch("main"))[0]
    assert offer.available == "1.1" and offer.compatible


@pytest.mark.parametrize(
    ("before", "after", "breaking"),
    [("1.0", "1.1", False), ("1.0", "2.0", True), ("0.4", "0.4.2", False), ("0.4", "0.5", True)],
)
def test_a_release_that_leaves_its_line_is_marked_breaking(
    shop, plugins_dir, installer, before, after, breaking
):
    shop.publish(A, before)
    shop.publish(A, after)
    install(shop, A, before)
    assert updates.offers_from(catalogue.fetch("main"))[0].breaking is breaking


def test_the_check_keeps_the_answer_and_the_pages_read_it_without_a_request(
    shop, plugins_dir, installer, admin, client
):
    shop.publish(A, "1.0")
    shop.publish(A, "1.1")
    install(shop, A, "1.0")
    updates.check()
    assert CatalogueAnswer.objects.get(name="main").ok

    shop.requests.clear()
    client.force_login(admin)
    overview = client.get(reverse("server:overview")).content.decode()
    page = client.get(reverse("server:plugins")).content.decode()
    assert "1 plugin update: postulo-example 1.1" in overview
    assert "postulo-example" in page and "1.0 → 1.1" in page
    assert shop.requests == []  # reading what was kept is not a request


def test_an_update_applied_since_the_check_is_not_shown_again(shop, plugins_dir, installer):
    shop.publish(A, "1.0")
    shop.publish(A, "1.1")
    install(shop, A, "1.0")
    updates.check()
    assert updates.summary()["count"] == 1
    install(shop, A, "1.1")
    assert updates.summary()["count"] == 0 and updates.pending() == []


def test_the_check_fetches_only_the_signed_index_and_installs_nothing(shop, plugins_dir, installer):
    shop.publish(A, "1.0")
    shop.publish(A, "1.1")
    install(shop, A, "1.0")
    shop.requests.clear()
    updates.check()
    assert sorted(shop.requests) == ["index.json", "index.json.sig"]
    assert not shop.asked_for_a_wheel() and version_of(A) == "1.0"


def test_a_catalogue_that_cannot_be_used_is_kept_as_the_reason_and_offers_nothing(
    shop, plugins_dir, installer, admin, client
):
    shop.publish(A, "1.0")
    shop.publish(A, "1.1")
    install(shop, A, "1.0")
    updates.check()
    shop.expires = timezone.now() - dt.timedelta(days=1)
    updates.check()
    row = CatalogueAnswer.objects.get(name="main")
    assert not row.ok and "expired on" in row.error and row.offers == []
    assert updates.pending() == []
    client.force_login(admin)
    html = client.get(reverse("server:plugins")).content.decode()
    assert 'data-update-problem="main"' in html and "cannot be used" in html


# --------------------------------------------------------------- telling people


@pytest.fixture
def told(monkeypatch):
    sent = []
    monkeypatch.setattr(
        "postulo.notifications.inbox.leave",
        lambda user, notification: sent.append((user.pk, notification.title, notification.body)),
    )
    return sent


def test_every_administrator_is_told_once_per_new_release(
    shop, plugins_dir, installer, admin, user, other_user, told
):
    other_user.is_staff = True
    other_user.save()
    shop.publish(A, "1.0")
    shop.publish(A, "1.1")
    install(shop, A, "1.0")

    updates.check()
    updates.check()
    # The two administrators, once each; the person who is not one is not told; no repeat.
    assert sorted(pk for pk, _title, _body in told) == sorted([admin.pk, other_user.pk])
    assert "postulo-example 1.1" in told[0][2]

    shop.publish(A, "1.2")
    updates.check()
    assert len(told) == 4


def test_a_held_plugins_newer_release_is_not_announced_unless_it_is_a_security_fix(
    shop, plugins_dir, installer, admin, told
):
    shop.publish(A, "1.0")
    shop.publish(A, "1.1")
    install(shop, A, "1.0")
    holds.hold(A, by=admin)
    updates.check()
    assert told == []
    shop.publish(A, "1.2", security=True, advisory="https://example.org/advisory")
    updates.check()
    assert len(told) == 1


# ------------------------------------------------------- the switch and the clock


def test_the_check_is_on_by_default_and_the_switch_stops_it(shop, plugins_dir, installer, admin):
    shop.publish(A, "1.0")
    assert updates.enabled() and updates.due()
    updates.switch(False, by=admin)
    assert not PluginUpdateSettings.current().checking
    assert not updates.enabled() and not updates.due()
    shop.requests.clear()
    assert updates.start_if_due() is False
    assert updates.check_when_a_page_is_opened() is False
    assert shop.requests == []  # off means no request at all


def test_the_environment_wins_over_the_switch(
    shop, plugins_dir, monkeypatch, settings, client, admin
):
    monkeypatch.setenv("POSTULO_PLUGIN_UPDATE_CHECK", "off")
    settings.POSTULO_PLUGIN_UPDATE_CHECK = "off"
    assert updates.pinned_by_environment() and not updates.enabled()
    with pytest.raises(updates.UpdateError, match="POSTULO_PLUGIN_UPDATE_CHECK"):
        updates.switch(True)
    client.force_login(admin)
    html = client.get(reverse("server:plugins")).content.decode()
    assert "POSTULO_PLUGIN_UPDATE_CHECK is set in the environment" in html


def test_it_runs_once_a_day(shop, plugins_dir, installer):
    shop.publish(A, "1.0")
    assert updates.start_if_due()
    assert not updates.due()
    CatalogueAnswer.objects.update(checked_at=timezone.now() - dt.timedelta(hours=25))
    assert updates.due()


def test_the_scheduler_hands_it_to_the_worker_once(shop, plugins_dir, installer):
    shop.publish(A, "1.0")
    queued = []
    assert updates.start_if_due(queue=lambda: queued.append(1))
    assert not updates.start_if_due(queue=lambda: queued.append(1))  # the lease is held
    assert queued == [1]


def test_with_no_scheduler_a_server_page_is_the_clock_and_with_one_it_is_not(
    shop, plugins_dir, installer, admin, client, settings, tmp_path
):
    shop.publish(A, "1.0")
    settings.POSTULO_SCHEDULER_HEARTBEAT = tmp_path / "beat"
    client.force_login(admin)
    scheduler.beat()
    client.get(reverse("server:overview"))
    assert shop.requests == [] and not CatalogueAnswer.objects.exists()

    (tmp_path / "beat").unlink()
    client.get(reverse("server:overview"))
    assert sorted(shop.requests) == ["index.json", "index.json.sig"]
    shop.requests.clear()
    client.get(reverse("server:overview"))
    assert shop.requests == []  # at most once a day


# ------------------------------------------------------------------- applying


def test_update_one_installs_the_newer_release_and_says_who_and_what(
    shop, plugins_dir, installer, admin, client, caplog
):
    shop.publish(A, "1.0")
    shop.publish(A, "1.1")
    install(shop, A, "1.0")
    client.force_login(admin)
    with caplog.at_level(logging.WARNING, logger="postulo.plugins.audit"):
        text = post(client, action="update", name=A).content.decode()
    assert version_of(A) == "1.1"
    assert installing.installed(A).installed_by == admin.get_username()
    assert "was updated from 1.0 to 1.1" in text
    assert "Restart Postulo to finish" in text  # the test server is not gunicorn
    assert any(
        "updated from 1.0 to 1.1" in record.getMessage()
        and admin.get_username() in record.getMessage()
        for record in caplog.records
    )


def test_a_gunicorn_server_is_asked_to_reload_and_anything_else_to_restart(monkeypatch):
    sent = []
    monkeypatch.setattr(
        signal, "SIGHUP", signal.SIGHUP if hasattr(signal, "SIGHUP") else 1, raising=False
    )
    monkeypatch.setattr("os.kill", lambda pid, number: sent.append((pid, number)))
    assert updates.reload_web("gunicorn/23.0.0") is True
    assert sent and sent[0][1] == signal.SIGHUP
    sent.clear()
    assert updates.reload_web("WSGIServer/0.2") is False and sent == []


def test_a_gunicorn_update_says_the_web_processes_are_reloading(
    shop, plugins_dir, installer, admin, client, monkeypatch
):
    shop.publish(A, "1.0")
    shop.publish(A, "1.1")
    install(shop, A, "1.0")
    monkeypatch.setattr(updates, "reload_web", lambda software: True)
    client.force_login(admin)
    assert "web processes are reloading" in post(client, action="update", name=A).content.decode()


def test_the_page_shows_what_is_loaded_against_what_is_installed_until_they_agree(
    shop, plugins_dir, installer, admin, client, monkeypatch
):
    shop.publish(A, "1.0")
    install(shop, A, "1.0")
    client.force_login(admin)
    monkeypatch.setattr(updates, "loaded_version", lambda name: "0.9")
    assert "data-restart-pending" in client.get(reverse("server:plugins")).content.decode()
    monkeypatch.setattr(updates, "loaded_version", lambda name: "1.0")
    assert "data-restart-pending" not in client.get(reverse("server:plugins")).content.decode()


def two_plugins(shop):
    shop.publish(A, "1.0")
    shop.publish(A, "1.1")
    shop.publish(B, "1.0")
    shop.publish(B, "1.1")


def test_update_all_updates_everything_with_a_plain_compatible_update(
    shop, plugins_dir, installer, admin
):
    two_plugins(shop)
    install(shop, A, "1.0")
    install(shop, B, "1.0")
    result = updates.apply(by=admin)
    assert sorted(result.updated) == [(A, "1.0", "1.1"), (B, "1.0", "1.1")]
    assert version_of(A) == version_of(B) == "1.1"


def test_update_all_leaves_a_held_plugin_and_a_breaking_release_alone(
    shop, plugins_dir, installer, admin
):
    two_plugins(shop)
    shop.publish(B, "2.0")
    install(shop, A, "1.0")
    install(shop, B, "1.0")
    holds.hold(A, by=admin)
    result = updates.apply(by=admin)
    assert result.updated == [] and len(result.skipped) == 2
    assert version_of(A) == version_of(B) == "1.0"
    # Asked for on purpose: the breaking one goes, the held one still does not.
    result = updates.apply(by=admin, include_breaking=True)
    assert result.updated == [(B, "1.0", "2.0")] and version_of(A) == "1.0"


def test_a_held_plugin_updates_by_hand_only_when_asked_to(shop, plugins_dir, installer, admin):
    two_plugins(shop)
    install(shop, A, "1.0")
    holds.hold(A, by=admin)
    assert updates.apply([A], by=admin).updated == []
    assert updates.apply([A], by=admin, include_held=True).updated == [(A, "1.0", "1.1")]
    assert holds.get(A).version == "1.1"  # the hold follows what was chosen


def test_the_page_asks_before_updating_a_held_plugin(shop, plugins_dir, installer, admin, client):
    two_plugins(shop)
    install(shop, A, "1.0")
    holds.hold(A, by=admin)
    client.force_login(admin)
    assert "is held at 1.0" in post(client, action="update", name=A).content.decode()
    assert version_of(A) == "1.0"
    post(client, action="update", name=A, confirm_held="1")
    assert version_of(A) == "1.1"


def test_a_plugin_switched_off_stays_off_after_it_is_updated(shop, plugins_dir, installer, admin):
    two_plugins(shop)
    install(shop, A, "1.0")
    installing.set_disabled(A, True)
    updates.apply([A], by=admin)
    assert version_of(A) == "1.1" and installing.installed(A).disabled


def test_a_failure_puts_the_whole_run_back_and_names_the_plugin(
    shop, plugins_dir, installer, admin, monkeypatch, caplog
):
    two_plugins(shop)
    install(shop, A, "1.0")
    install(shop, B, "1.0")
    fake = installing.run_install

    def fails_for_b(target, wheel, constraint_file):
        if "postulo_other-1.1" in wheel.name:
            raise InstallError("pip fell over")
        return fake(target, wheel, constraint_file)

    monkeypatch.setattr(installing, "run_install", fails_for_b)
    with caplog.at_level(logging.WARNING, logger="postulo.plugins.audit"):
        with pytest.raises(updates.UpdateError, match="postulo-other was not updated"):
            updates.apply(by=admin)
    # A went first and was installed; the failure of B took it back as well.
    assert version_of(A) == version_of(B) == "1.0"
    assert not list(plugins_dir.glob("postulo_example-1.1*"))
    assert any("rolled back" in record.getMessage() for record in caplog.records)


def test_a_plugin_that_will_not_import_rolls_the_run_back(
    shop, plugins_dir, installer, admin, monkeypatch
):
    two_plugins(shop)
    install(shop, A, "1.0")
    install(shop, B, "1.0")
    monkeypatch.setattr(
        installing,
        "verify_imports",
        lambda names: ["it broke"] if any("other" in name for name in names) else [],
    )
    with pytest.raises(updates.UpdateError, match="could not be loaded"):
        updates.apply(by=admin)
    assert version_of(A) == version_of(B) == "1.0"


def test_the_last_way_back_survives_a_failed_run(shop, plugins_dir, installer, admin, monkeypatch):
    """A run that fails gives back the snapshot that was there, as a failed install does."""
    two_plugins(shop)
    install(shop, A, "1.0")
    install(shop, A, "1.1")  # leaves a snapshot to go back to
    monkeypatch.setattr(installing, "verify_imports", lambda names: ["broken"])
    shop.publish(A, "1.2")
    with pytest.raises(updates.UpdateError):
        updates.apply([A], by=admin)
    assert version_of(A) == "1.1" and installing.can_roll_back()


def test_every_wheel_is_fetched_and_checked_before_anything_changes(
    shop, plugins_dir, installer, admin
):
    two_plugins(shop)
    install(shop, A, "1.0")
    install(shop, B, "1.0")
    installer.clear()
    del shop.wheels[next(name for name in shop.wheels if "postulo_other-1.1" in name)]
    with pytest.raises(updates.UpdateError, match="nothing changed"):
        updates.apply(by=admin)
    assert installer == [] and version_of(A) == "1.0"


def test_a_wheel_that_is_not_the_one_the_index_signed_is_never_installed(
    shop, plugins_dir, installer, admin
):
    two_plugins(shop)
    install(shop, A, "1.0")
    name = next(name for name in shop.wheels if "postulo_example-1.1" in name)
    shop.wheels[name] = shop.wheels[name] + b"tampered"
    with pytest.raises(updates.UpdateError, match="checksum"):
        updates.apply([A], by=admin)
    assert version_of(A) == "1.0"


def test_an_index_with_a_bad_signature_is_never_used_to_update(
    shop, plugins_dir, installer, admin, monkeypatch
):
    two_plugins(shop)
    install(shop, A, "1.0")
    shop.key = Ed25519PrivateKey.generate()  # signs with a key nobody configured
    with pytest.raises(updates.UpdateError, match="signature"):
        updates.apply([A], by=admin)
    assert version_of(A) == "1.0"


def test_updates_go_in_dependency_order(shop, plugins_dir, installer, admin):
    shop.publish(A, "1.0")
    shop.publish(A, "1.1")
    shop.publish(B, "1.0")
    shop.publish(B, "1.1", requires=[f"{A}>=1.1"])
    install(shop, A, "1.0")
    install(shop, B, "1.0")
    installer.clear()
    updates.apply(by=admin)
    assert [call["wheel"].name.split("-")[0] for call in installer] == [
        "postulo_example",
        "postulo_other",
    ]


def test_a_plugin_another_one_needs_is_not_updated_past_what_it_accepts(
    shop, plugins_dir, installer, admin
):
    shop.publish(A, "1.0")
    shop.publish(A, "2.0")
    shop.publish(B, "1.0", requires=[f"{A}<2"])
    install(shop, A, "1.0")
    install(shop, B, "1.0")
    with pytest.raises(updates.UpdateError, match="postulo-other needs postulo-example<2"):
        updates.apply([A], by=admin)
    assert version_of(A) == "1.0"


def test_nothing_to_update_says_so(shop, plugins_dir, installer, admin, client):
    shop.publish(A, "1.0")
    install(shop, A, "1.0")
    client.force_login(admin)
    assert "There was nothing to update." in post(client, action="update_all").content.decode()


def test_only_an_administrator_can_press_any_of_it(shop, plugins_dir, client, user):
    client.force_login(user)
    for action in ("update", "update_all", "update_check"):
        response = client.post(reverse("server:plugin_action"), {"action": action})
        assert response.status_code in (302, 403)
        assert "plugins" not in response.get("Location", "")


def test_the_listing_says_installed_and_offered_and_labels_an_update(
    shop, plugins_dir, installer, admin, client
):
    shop.publish(A, "1.0")
    shop.publish(A, "1.1")
    install(shop, A, "1.0")
    client.force_login(admin)
    client.post(reverse("server:plugin_action"), {"action": "refresh"})
    html = client.get(reverse("server:plugins")).content.decode()
    assert "(installed: 1.0)" in html and "postulo-example 1.1" in html
    # And the press kept its answer, as the daily check would have.
    assert CatalogueAnswer.objects.get(name="main").offers[0]["available"] == "1.1"
