"""Who may download a backup through the browser is the instance's choice (#810)."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from postulo.core import backups
from tests.recently import signed_in_recently

pytestmark = pytest.mark.django_db

User = get_user_model()


def make(username, **extra):
    return User.objects.create_user(
        email=f"{username}@example.org", password="not-a-real-password", username=username, **extra
    )


@pytest.fixture
def archive(tmp_path, settings):
    settings.POSTULO_BACKUP_DIR = str(tmp_path)
    (tmp_path / "postulo-backup-20260101-000000.tar.gz").write_bytes(b"\x1f\x8b not really")
    return "postulo-backup-20260101-000000.tar.gz"


def get(client, who, name):
    signed_in_recently(client, who)
    response = client.get(reverse("server:backup_download", args=[name]))
    response.close()  # a streamed file is open until it is
    return response


def test_by_default_every_administrator_may(client, archive, settings):
    assert get(client, make("adm", is_staff=True), archive).status_code == 200


def test_operator_only_refuses_an_ordinary_administrator(client, archive, settings):
    settings.POSTULO_BACKUP_DOWNLOAD = "operator"
    assert get(client, make("adm", is_staff=True), archive).status_code == 404
    assert get(client, make("op", is_staff=True, is_superuser=True), archive).status_code == 200


def test_console_refuses_everybody_and_the_page_draws_no_link(client, archive, settings):
    settings.POSTULO_BACKUP_DOWNLOAD = "console"
    operator = make("op", is_staff=True, is_superuser=True)
    assert get(client, operator, archive).status_code == 404
    page = client.get(reverse("server:backups")).content.decode()
    assert archive in page
    assert reverse("server:backup_download", args=[archive]) not in page


def test_a_word_the_instance_does_not_know_grants_nobody(client, archive, settings):
    """`operators` is a typo for `operator`; it must not fall back to every administrator (#978)."""
    settings.POSTULO_BACKUP_DOWNLOAD = "operators"
    assert get(client, make("adm", is_staff=True), archive).status_code == 404
    assert get(client, make("op", is_staff=True, is_superuser=True), archive).status_code == 404


def test_start_up_refuses_a_word_the_setting_does_not_know(settings):
    from django.core.checks import run_checks

    settings.POSTULO_BACKUP_DOWNLOAD = "operators"
    assert [e for e in run_checks() if e.id == "postulo.E001" and "BACKUP_DOWNLOAD" in e.msg]
    settings.POSTULO_BACKUP_DOWNLOAD = "operator"
    assert not [e for e in run_checks() if "BACKUP_DOWNLOAD" in e.msg]


def test_the_threat_model_names_the_exception():
    from pathlib import Path

    text = (Path(__file__).resolve().parent.parent / "docs" / "THREAT-MODEL.md").read_text(
        encoding="utf-8"
    )
    assert "POSTULO_BACKUP_DOWNLOAD" in text
    assert backups  # the module the setting governs
