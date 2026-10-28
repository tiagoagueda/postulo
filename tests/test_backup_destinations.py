"""Backups that leave the machine: the destination kind, the encryption, the sending (#325).

A fake destination keeps what it is sent in a dictionary, so every test can see exactly what
arrived, and fail when told to. Archives are the hand-built ones the Backups page tests use:
a manifest and a database member that verify. The encryption is real -- `pyrage`, to a
recipient generated here -- and a test decrypts what the destination received and compares
it with the archive on the disk.
"""

from __future__ import annotations

import datetime as dt
import itertools
import logging
import time

import pyrage
import pytest
from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone
from pyrage import x25519

from postulo.core import backups, metrics, offsite
from postulo.core.backup import BackupReport
from postulo.core.memo import forget_current
from postulo.core.models import OffsiteBackup, OffsiteState, SiteSettings
from postulo.plugins import http, registry
from postulo.plugins.api import (
    BackupArchive,
    BackupDestinationPlugin,
    BackupManifest,
    BackupRef,
    FieldSpec,
)
from postulo.plugins.api import (
    TestResult as Answer,
)
from tests.test_backups_page import make_archive

pytestmark = pytest.mark.django_db

User = get_user_model()
PASSWORD = "a-fairly-long-password-42"


# ------------------------------------------------------------------- the fake destination


class Vault:
    """What the fake destination holds and how it behaves, reset for every test."""

    def __init__(self) -> None:
        self.stored: dict[str, bytes] = {}
        self.manifests: dict[str, BackupManifest] = {}
        self.foreign: list[BackupRef] = []
        self.fail = ""
        self.calls: list[str] = []
        self.deadlines: list[float | None] = []
        self.unlisted: set[str] = set()


VAULT = Vault()


class FakeVault:
    name = "fake-vault"
    version = "1.0"
    kind = "backup-destination"
    label = "Fake vault"
    description = "Keeps archives in a dictionary, for the tests."

    def config_fields(self) -> list[FieldSpec]:
        return [
            FieldSpec("bucket", "Bucket"),
            FieldSpec("token", "Token", type="password", secret=True),
        ]

    def validate(self, config: dict) -> dict:
        return {"bucket": ["No spaces in a bucket."]} if " " in config.get("bucket", "") else {}

    def test(self, config: dict) -> Answer:
        VAULT.calls.append("test")
        return Answer(bool(config.get("token")), "The vault answered.")

    def send(self, archive: BackupArchive, manifest: BackupManifest, config: dict) -> BackupRef:
        VAULT.calls.append("send")
        VAULT.deadlines.append(http._deadline.get())
        if VAULT.fail:
            raise RuntimeError(VAULT.fail)
        key = f"{config['bucket']}/{archive.name}"
        VAULT.stored[key] = b"".join(archive.chunks(size=4096))
        VAULT.manifests[key] = manifest
        return BackupRef(id=key, label=archive.name)

    def list(self, config: dict) -> list[BackupRef]:
        VAULT.calls.append("list")
        mine = [BackupRef(id=key, label=key) for key in VAULT.stored if key not in VAULT.unlisted]
        return mine + VAULT.foreign

    def delete(self, reference: BackupRef, config: dict) -> None:
        VAULT.calls.append(f"delete {reference.id}")
        VAULT.stored.pop(reference.id)


@pytest.fixture(autouse=True)
def _a_directory_of_its_own(settings, tmp_path):
    settings.POSTULO_BACKUP_DIR = tmp_path / "backups"
    settings.POSTULO_BACKUP_DIR.mkdir()
    settings.POSTULO_BACKGROUND_WORK = False
    return settings.POSTULO_BACKUP_DIR


@pytest.fixture(autouse=True)
def vault():
    global VAULT
    VAULT = Vault()
    registry.register_builtin("backup-destination", FakeVault)
    try:
        yield VAULT
    finally:
        registry.unregister_builtin("backup-destination", FakeVault)


@pytest.fixture
def identity():
    return x25519.Identity.generate()


def configure(**fields) -> SiteSettings:
    SiteSettings.objects.update_or_create(pk=1, defaults=fields)
    forget_current()
    return SiteSettings.get()


@pytest.fixture
def chosen(identity):
    """The fake vault chosen, with a bucket, a token and a recipient."""
    row = configure(
        backup_destination="fake-vault",
        backup_destination_config={"bucket": "nightly"},
        backup_recipient=str(identity.to_public()),
    )
    row.backup_destination_secrets = {"token": "s3cr3t-token-value"}
    row.save()
    forget_current()
    return row


@pytest.fixture
def admin(db):
    person = User.objects.create_user(
        email="admin@example.org", password=PASSWORD, username="admin-one", is_staff=True
    )
    EmailAddress.objects.create(user=person, email=person.email, verified=True, primary=True)
    return person


def no_scratch_left() -> bool:
    return not any(path.name.startswith(".offsite-") for path in backups.directory().iterdir())


# ----------------------------------------------------------------------- the kind


def test_the_kind_is_registered_like_a_transport():
    from postulo.plugins import kinds, policy

    assert registry.GROUPS["backup-destination"] == "postulo.backups"
    assert "backup-destination" in policy.UNGOVERNED_KINDS
    assert "backup-destination" not in policy.GOVERNED_KINDS
    assert kinds.label_for("backup-destination") == "Backup destination"
    assert kinds.tone_for("backup-destination") == "teal"
    assert isinstance(FakeVault(), BackupDestinationPlugin)
    assert policy.is_ungoverned("fake-vault")


def test_installing_a_destination_does_not_start_sending(identity):
    configure(backup_recipient=str(identity.to_public()))
    archive = make_archive()

    assert offsite.selected() is None, "nobody chose it"
    assert offsite.queue(archive) is None
    assert not OffsiteBackup.objects.exists()


# ------------------------------------------------------------------ the encryption


def test_what_arrives_is_the_archive_encrypted_to_the_recipient(chosen, identity, vault):
    archive = make_archive()
    offsite.queue(archive)

    assert offsite.send_pending() == (1, 0)

    key = f"nightly/{archive.name}.age"
    received = vault.stored[key]
    assert received.startswith(b"age-encryption.org/v1\n")
    assert archive.read_bytes() not in received
    assert pyrage.decrypt(received, [identity]) == archive.read_bytes()
    assert no_scratch_left(), "the encrypted copy is deleted once it has gone"
    row = OffsiteBackup.objects.get()
    assert row.state == OffsiteState.SENT and row.remote_label == f"{archive.name}.age"
    assert vault.manifests[key].archive == archive.name
    assert ("applications", 5) in vault.manifests[key].counts


@pytest.mark.parametrize(
    "text",
    [
        "",
        "age1foo",
        "AGE1" + "Q" * 58,
        "age1yubikey1qwt50d05nh5vutpdzmlg5wn80xq5negm4uj9ghv0snvdd3yysf5yw3rhl3t",
        "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl",
    ],
)
def test_only_an_x25519_recipient_is_accepted(text):
    with pytest.raises(offsite.RecipientRefused):
        offsite.parse_recipient(text)


def test_a_recipient_with_a_bad_checksum_is_refused(identity):
    good = str(identity.to_public())
    last = "q" if good[-1] != "q" else "p"

    with pytest.raises(offsite.RecipientRefused):
        offsite.parse_recipient(good[:-1] + last)
    assert offsite.parse_recipient(f"  {good}\n") is not None


def test_nothing_is_sent_without_a_recipient(chosen, vault):
    configure(backup_recipient="")
    offsite.queue(make_archive())

    assert offsite.send_pending() == (0, 1)

    assert "send" not in vault.calls
    assert "unencrypted" in OffsiteBackup.objects.get().last_error


# ---------------------------------------------------------------------- queueing


def test_an_archive_taken_by_the_page_or_the_schedule_is_queued(chosen, monkeypatch):
    written = []

    def write_backup():
        written.append(make_archive())
        return BackupReport(path=written[-1], bytes=written[-1].stat().st_size)

    monkeypatch.setattr(backups, "write_backup", write_backup)

    path = backups.take()

    row = OffsiteBackup.objects.get()
    assert row.archive == path.name and row.state == OffsiteState.PENDING
    assert row.destination == "fake-vault"


def test_an_uploaded_archive_or_one_elsewhere_is_never_queued(chosen, tmp_path):
    uploaded = make_archive(prefix="upload")
    elsewhere = tmp_path / "postulo-backup-20260101-030000.tar.gz"
    elsewhere.write_bytes(b"not ours to send")

    assert offsite.queue(uploaded) is None
    assert offsite.queue(elsewhere) is None


def test_the_command_queues_unless_told_not_to(chosen, monkeypatch, tmp_path):
    from postulo.core.management.commands import backup as command

    stamps = iter(["20260101-030000", "20260102-030000", "20260103-030000"])

    def write_backup(target=None, **kwargs):
        if target:
            path = tmp_path / "elsewhere.tar.gz"
            path.write_bytes(make_archive(next(stamps)).read_bytes())
        else:
            path = make_archive(next(stamps))
        return BackupReport(path=path, bytes=path.stat().st_size)

    monkeypatch.setattr(command, "write_backup", write_backup)

    call_command("backup", "--no-send")
    assert not OffsiteBackup.objects.exists()

    call_command("backup")
    assert list(OffsiteBackup.objects.values_list("archive", flat=True)) == [
        "postulo-backup-20260102-030000.tar.gz"
    ]

    call_command("backup", str(tmp_path / "elsewhere.tar.gz"))
    assert OffsiteBackup.objects.count() == 1, "one written elsewhere is the operator's to carry"


# ------------------------------------------------------------- retrying, the lease


def test_a_failure_is_retried_with_a_growing_wait_and_then_waits_for_a_person(chosen, vault):
    vault.fail = "The vault is full."
    offsite.queue(make_archive())
    row = OffsiteBackup.objects.get()
    waits = []

    for _attempt in range(offsite.MAX_ATTEMPTS):
        before = timezone.now()
        assert offsite.send_pending(limit=1) == (0, 1)
        row.refresh_from_db()
        waits.append(row.next_attempt_at - before)
        assert not offsite.pending().exists(), "not due again at once"
        OffsiteBackup.objects.filter(pk=row.pk).update(next_attempt_at=timezone.now())

    assert row.last_error == "The vault is full.", "in the plugin's own words"
    assert all(later > earlier * 1.9 for earlier, later in itertools.pairwise(waits))
    assert waits[0] >= offsite.FIRST_RETRY - dt.timedelta(seconds=5)
    assert not offsite.pending().exists(), "after the last attempt, it waits for a person"
    assert SiteSettings.get().backup_offsite_failures == offsite.MAX_ATTEMPTS

    vault.fail = ""
    offsite.send_again(row.archive)
    assert offsite.send_pending() == (1, 0)
    assert SiteSettings.get().backup_offsite_last_error == ""


def test_a_row_is_claimed_once_and_the_lease_runs_out(chosen):
    offsite.queue(make_archive())
    row = OffsiteBackup.objects.get()
    now = timezone.now()

    assert offsite.claim(row, now) is True
    assert offsite.claim(OffsiteBackup.objects.get(), now) is False, "somebody holds it"
    assert not offsite.pending(now).exists()

    later = now + offsite.SENDING_LEASE + dt.timedelta(seconds=1)
    assert offsite.pending(later).exists(), "a sender that died gives it back"
    assert offsite.claim(OffsiteBackup.objects.get(), later) is True


def test_a_row_read_before_another_pass_tried_it_is_not_sent_again(chosen):
    offsite.queue(make_archive())
    stale = OffsiteBackup.objects.get()
    OffsiteBackup.objects.filter(pk=stale.pk).update(attempts=1, state=OffsiteState.FAILED)

    assert offsite.claim(stale) is False


def test_the_send_ends_before_its_claim_does(chosen, vault):
    offsite.queue(make_archive())
    started = time.monotonic()

    offsite.send_pending()

    (deadline,) = vault.deadlines
    assert deadline is not None, "the upload runs inside a deadline"
    lease = offsite.SENDING_LEASE.total_seconds()
    assert deadline < started + lease, "and the deadline falls inside the lease"
    assert deadline > started + lease - offsite.LEASE_MARGIN.total_seconds() - 60


def test_the_scheduler_sends_in_the_worker_when_there_is_one(chosen, vault):
    offsite.queue(make_archive())
    queued = []

    assert offsite.start_sending(queue=lambda: queued.append(1)) is True
    assert queued == [1] and "send" not in vault.calls

    assert offsite.start_sending() is True
    assert vault.calls.count("send") == 1
    assert offsite.start_sending() is False, "nothing left to send"


def test_a_pass_of_the_scheduler_sends_after_its_own_work(chosen, vault):
    offsite.queue(make_archive())

    call_command("send_due_reminders")

    assert OffsiteBackup.objects.get().state == OffsiteState.SENT


# --------------------------------------------------------------------- retention


def sent_rows(count: int, vault) -> list[OffsiteBackup]:
    """``count`` archives already at the vault, oldest first."""
    rows = []
    for day in range(1, count + 1):
        archive = make_archive(f"202501{day:02d}-030000")
        key = f"nightly/{archive.name}.age"
        vault.stored[key] = b"old"
        rows.append(
            OffsiteBackup.objects.create(
                archive=archive.name,
                destination="fake-vault",
                state=OffsiteState.SENT,
                sent_at=timezone.now() - dt.timedelta(days=40 - day),
                remote_id=key,
                remote_label=archive.name,
            )
        )
    return rows


def test_retention_follows_a_confirmed_send_and_deletes_only_what_was_listed_and_ours(
    chosen, vault
):
    configure(backup_remote_keep=2)
    old = sent_rows(3, vault)
    vault.foreign = [BackupRef(id="nightly/somebody-elses.tar", label="somebody-elses.tar")]
    vault.stored["nightly/somebody-elses.tar"] = b"theirs"
    vault.unlisted = {old[0].remote_id}  # the destination does not list the oldest

    offsite.queue(make_archive("20260101-030000"))
    offsite.send_pending()

    deleted = [call for call in vault.calls if call.startswith("delete")]
    assert deleted == [f"delete {old[1].remote_id}"], "the newest two kept, the unlisted left"
    assert "nightly/somebody-elses.tar" in vault.stored, "never somebody else's"
    assert old[0].remote_id in vault.stored, "never what list() did not return"
    old[1].refresh_from_db()
    assert old[1].state == OffsiteState.REMOVED


def test_no_retention_after_a_failed_send(chosen, vault):
    configure(backup_remote_keep=1)
    sent_rows(3, vault)
    vault.fail = "Refused."
    offsite.queue(make_archive("20260101-030000"))

    offsite.send_pending()

    assert "list" not in vault.calls and len(vault.stored) == 3


def test_zero_keeps_everything_there_and_blank_follows_the_local_number(chosen, vault):
    sent_rows(3, vault)
    configure(backup_remote_keep=0)
    offsite.queue(make_archive("20260101-030000"))
    offsite.send_pending()
    assert not any(call.startswith("delete") for call in vault.calls)

    configure(backup_remote_keep=None, backup_keep=3)
    assert offsite.remote_keep() == 3


def test_a_failed_send_never_costs_the_local_archive(chosen, vault):
    configure(backup_keep=1)
    waiting = make_archive("20260101-030000")
    vault.fail = "Unreachable."
    offsite.queue(waiting)
    offsite.send_pending()
    newer = make_archive("20260102-030000")

    gone = backups.apply_retention(1)

    assert gone == [] and waiting.exists() and newer.exists()


def test_a_sent_archive_is_rotated_as_before(chosen, vault):
    sent = make_archive("20260101-030000")
    offsite.queue(sent)
    offsite.send_pending()
    make_archive("20260102-030000")

    assert backups.apply_retention(1) == [sent.name]


def test_a_send_logs_what_went_where_and_a_remote_delete_says_so(chosen, vault, caplog):
    configure(backup_remote_keep=1)
    old = sent_rows(1, vault)

    with caplog.at_level(logging.INFO):
        offsite.queue(make_archive("20260101-030000"))
        offsite.send_pending()

    text = "\n".join(record.getMessage() for record in caplog.records)
    assert "postulo-backup-20260101-030000.tar.gz sent to fake-vault" in text
    assert f"Retention deleted backup {old[0].archive}" in text
    assert "s3cr3t-token-value" not in text


# ------------------------------------------------------------------- what staff see


def test_the_list_says_sent_waiting_or_failed(chosen, vault, client, admin):
    sent = make_archive("20260101-030000")
    failed = make_archive("20260102-030000")
    waiting = make_archive("20260103-030000")
    offsite.queue(sent)
    offsite.send_pending()
    vault.fail = "The vault is full."
    offsite.queue(failed)
    offsite.send_pending()
    offsite.queue(waiting)
    OffsiteBackup.objects.filter(archive=waiting.name).update(
        next_attempt_at=timezone.now() + dt.timedelta(hours=1)
    )
    client.force_login(admin)

    html = client.get(reverse("server:backups")).content.decode()

    assert 'data-offsite="sent"' in html and f"{sent.name}.age" in html
    assert 'data-offsite="failed"' in html and "The vault is full." in html
    assert 'data-offsite="pending"' in html
    assert reverse("server:backup_send", args=[failed.name]) in html
    assert reverse("server:backup_send", args=[sent.name]) not in html


def test_overview_says_so_when_a_send_failed(chosen, vault, client, admin):
    vault.fail = "Refused."
    offsite.queue(make_archive())
    offsite.send_pending()
    client.force_login(admin)

    html = client.get(reverse("server:overview")).content.decode()

    assert "data-backup-offsite-failed" in html


def test_send_queues_and_never_sends_in_the_request(chosen, vault, client, admin):
    archive = make_archive()
    client.force_login(admin)

    response = client.post(reverse("server:backup_send", args=[archive.name]))

    assert response.status_code == 302
    assert "send" not in vault.calls
    assert offsite.pending().filter(archive=archive.name).exists()


def test_the_metrics_carry_the_two_offsite_numbers(chosen, vault):
    offsite.queue(make_archive("20260101-030000"))
    offsite.send_pending()
    vault.fail = "Refused."
    offsite.queue(make_archive("20260102-030000"))
    offsite.send_pending()
    forget_current()

    text = metrics.render()

    assert "postulo_backup_offsite_failures_total 1" in text
    assert "# TYPE postulo_backup_offsite_failures_total counter" in text
    assert "postulo_backup_offsite_last_success_timestamp_seconds 0" not in text
    assert "# TYPE postulo_backup_offsite_last_success_timestamp_seconds gauge" in text


# --------------------------------------------------------------------- the form


def test_the_form_is_drawn_from_the_plugins_fields(chosen, client, admin):
    client.force_login(admin)

    html = client.get(reverse("server:backup_destination")).content.decode()

    assert 'name="destination__bucket"' in html and 'value="nightly"' in html
    assert 'name="destination__token"' in html
    assert "s3cr3t-token-value" not in html, "a secret is never drawn back"


def test_a_blank_secret_is_kept_and_a_new_one_replaces_it(chosen, client, admin, identity):
    client.force_login(admin)
    url = reverse("server:backup_destination")
    form = {
        "backup_destination": "fake-vault",
        "backup_recipient": str(identity.to_public()),
        "backup_remote_keep": "",
        "destination__bucket": "weekly",
        "destination__token": "",
    }

    assert client.post(url, form).status_code == 302
    row = SiteSettings.get()
    assert row.backup_destination_config == {"bucket": "weekly"}
    assert row.backup_destination_secrets == {"token": "s3cr3t-token-value"}
    assert "s3cr3t" not in row.backup_destination_secrets_encrypted, "encrypted at rest"

    client.post(url, {**form, "destination__token": "another-one"})
    assert SiteSettings.get().backup_destination_secrets == {"token": "another-one"}


def test_a_destination_without_a_recipient_is_refused(client, admin):
    client.force_login(admin)

    response = client.post(
        reverse("server:backup_destination"),
        {
            "backup_destination": "fake-vault",
            "backup_recipient": "",
            "destination__bucket": "nightly",
            "destination__token": "t",
        },
    )

    assert response.status_code == 200
    assert "nothing leaves this server unencrypted" in response.content.decode()
    assert SiteSettings.get().backup_destination == ""


def test_choosing_it_draws_its_fields_and_saves_nothing_until_they_are_filled(
    client, admin, identity
):
    client.force_login(admin)
    url = reverse("server:backup_destination")
    form = {"backup_destination": "fake-vault", "backup_recipient": str(identity.to_public())}

    response = client.post(url, form)

    assert response.status_code == 200 and 'name="destination__bucket"' in response.content.decode()
    assert SiteSettings.get().backup_destination == ""

    response = client.post(url, {**form, "destination__bucket": "a b", "destination__token": "t"})
    assert "No spaces in a bucket." in response.content.decode(), "the plugin's validate()"


def test_none_forgets_the_settings_and_says_who_in_the_log(chosen, client, admin, caplog):
    client.force_login(admin)

    with caplog.at_level(logging.INFO):
        client.post(
            reverse("server:backup_destination"),
            {"backup_destination": "", "backup_recipient": chosen.backup_recipient},
        )

    row = SiteSettings.get()
    assert row.backup_destination == "" and row.backup_destination_secrets == {}
    assert row.backup_destination_config == {}
    assert any(
        "admin-one" in record.getMessage() and "removed" in record.getMessage()
        for record in caplog.records
    )


def test_the_test_button_asks_the_plugin(chosen, vault, client, admin):
    client.force_login(admin)

    response = client.post(reverse("server:backup_destination_test"), follow=True)

    assert vault.calls == ["test"]
    assert "The vault answered." in response.content.decode()


# ---------------------------------------------------------------- the published check


def test_the_published_check_accepts_a_destination_that_keeps_its_contract(tmp_path):
    from postulo.plugins.testing import assert_is_a_backup_destination

    assert_is_a_backup_destination(FakeVault(), {"bucket": "b"}, tmp_path=tmp_path)


def test_the_published_check_fails_a_destination_that_does_not_list_what_it_kept(tmp_path):
    from postulo.plugins.testing import assert_is_a_backup_destination

    class Forgetful(FakeVault):
        def list(self, config):
            return []

    with pytest.raises(AssertionError, match="is not listed"):
        assert_is_a_backup_destination(Forgetful(), {"bucket": "b"}, tmp_path=tmp_path)
