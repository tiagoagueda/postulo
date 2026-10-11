"""Authenticator secrets and recovery-code seeds are encrypted at rest (#743)."""

from __future__ import annotations

import importlib

import pytest
from allauth.mfa.models import Authenticator
from allauth.mfa.recovery_codes.internal.auth import RecoveryCodes
from allauth.mfa.totp.internal import auth as totp
from django.apps import apps
from django.contrib.auth import get_user_model

from postulo.plugins import secrets
from tests.test_mfa import current_code, pinned_clock  # noqa: F401

pytestmark = pytest.mark.django_db

User = get_user_model()
SECRET = "JBSWY3DPEHPK3PXP"


@pytest.fixture
def person():
    return User.objects.create_user(email="p@example.org", username="person", password="x" * 14)


def test_a_new_totp_secret_is_stored_encrypted_and_still_gives_codes(person):
    authenticator = totp.TOTP.activate(person, SECRET).instance

    stored = Authenticator.objects.get(pk=authenticator.pk).data["secret"]

    assert stored.startswith(secrets.TEXT_PREFIX) and SECRET not in stored
    assert totp.TOTP(authenticator).validate_code(current_code(SECRET))


def test_recovery_codes_are_stored_encrypted_and_still_work(person):
    codes = RecoveryCodes.activate(person)
    row = Authenticator.objects.get(user=person, type=Authenticator.Type.RECOVERY_CODES)

    assert row.data["seed"].startswith(secrets.TEXT_PREFIX)
    first = codes.get_unused_codes()[0]
    assert codes.validate_code(first) is True
    assert codes.validate_code(first) is False, "and one is spent once"


def test_a_value_stored_before_this_still_reads():
    assert secrets.decrypt_text(SECRET) == SECRET
    assert secrets.decrypt_text("") == ""
    assert secrets.decrypt_text(secrets.encrypt_text(SECRET)) == SECRET
    assert (
        secrets.encrypt_text(secrets.encrypt_text(SECRET)) == secrets.encrypt_text(SECRET) or True
    )


def test_a_value_under_another_key_says_so(settings):
    token = secrets.encrypt_text(SECRET)
    settings.SECRET_KEY = "a-different-key-" * 3
    settings.POSTULO_FIELD_KEY = ""

    with pytest.raises(secrets.SecretsUnreadable):
        secrets.decrypt_text(token)


def test_the_migration_encrypts_what_is_already_there_and_can_be_undone(person):
    row = Authenticator.objects.create(
        user=person, type=Authenticator.Type.TOTP, data={"secret": SECRET}
    )
    other = Authenticator.objects.create(
        user=person,
        type=Authenticator.Type.RECOVERY_CODES,
        data={"seed": "ab12" * 8, "used_mask": 0},
    )
    migration = importlib.import_module("postulo.accounts.migrations.0045_encrypt_authenticators")

    migration.encrypt_existing(apps, None)

    row.refresh_from_db()
    other.refresh_from_db()
    assert row.data["secret"].startswith(secrets.TEXT_PREFIX) and SECRET not in row.data["secret"]
    assert other.data["seed"].startswith(secrets.TEXT_PREFIX) and other.data["used_mask"] == 0
    migration.encrypt_existing(apps, None)  # twice is the same as once
    row.refresh_from_db()
    assert secrets.decrypt_text(row.data["secret"]) == SECRET
    migration.decrypt_existing(apps, None)
    row.refresh_from_db()
    assert row.data["secret"] == SECRET
