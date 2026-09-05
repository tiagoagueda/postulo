"""Who a person is to Postulo: a username, a full name, and addresses that were proven."""

import importlib
import io
import zipfile

import pytest
from allauth.account.models import EmailAddress
from django.apps import apps
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.urls import reverse

from postulo.accounts.models import Invite, unique_username
from postulo.accounts.validators import slug_from_email
from postulo.core.export import build_document, write_archive
from postulo.core.importer import load

pytestmark = pytest.mark.django_db

User = get_user_model()
PASSWORD = "a-fairly-long-password-42"

SIGNUP = {
    "first_name": "Alex",
    "last_name": "Morgan",
    "username": "alex.morgan",
    "email": "alex.morgan@example.org",
    "password1": PASSWORD,
    "password2": PASSWORD,
}


def verified(user):
    return EmailAddress.objects.create(user=user, email=user.email, verified=True, primary=True)


@pytest.fixture(autouse=True)
def _no_rate_limit_carried_over():
    # allauth rate-limits verification mail per address through the cache, which outlives
    # a test. Each test here starts with a clean slate, or a mail "already sent" by the
    # previous one would silently go missing.
    cache.clear()


@pytest.fixture
def staff_user(db):
    return User.objects.create_user(
        email="staff@example.org", password=PASSWORD, username="staff-member", is_staff=True
    )


# ------------------------------------------------------------------- usernames


@pytest.mark.parametrize(
    "email,expected",
    [
        ("alex.morgan@example.org", "alex.morgan"),
        ("Alex_Morgan+jobs@example.org", "alex_morgan-jobs"),
        ("ab@example.org", "ab0"),
        ("@example.org", "user"),
        ("__.--@example.org", "user"),
        ("a" * 50 + "@example.org", "a" * 32),
    ],
)
def test_a_username_is_suggested_from_the_address(email, expected):
    assert slug_from_email(email) == expected


def test_a_derived_username_gets_a_suffix_when_taken():
    User.objects.create_user(email="alex@example.org", password=PASSWORD)
    second = User.objects.create_user(email="alex@example.com", password=PASSWORD)
    third = User.objects.create_user(email="alex@example.net", password=PASSWORD)
    assert second.username == "alex2"
    assert third.username == "alex3"
    assert unique_username("a" * 32, lambda name: name == "a" * 32) == "a" * 31 + "2"


def test_usernames_are_one_spelling_per_person():
    user = User.objects.create_user(email="x@example.org", password=PASSWORD, username="Alex.M")
    assert user.username == "alex.m"
    user.username = "  ALEX.M "
    user.save()
    assert User.objects.get(pk=user.pk).username == "alex.m"


@pytest.mark.parametrize("bad", ["ab", "-abc", "abc-", "a b", "Alex", "a..", "a" * 33, "é"])
def test_the_username_rules(bad):
    user = User(email="x@example.org", username=bad, first_name="A", last_name="B")
    with pytest.raises(ValidationError):
        user.full_clean(exclude=["password"])


def test_a_superuser_can_be_created_the_way_createsuperuser_does_it():
    boss = User.objects.create_superuser(
        username="boss",
        email="boss@example.org",
        first_name="B",
        last_name="Oss",
        password=PASSWORD,
    )
    assert boss.is_staff and boss.is_superuser
    assert boss.username == "boss"
    assert User.USERNAME_FIELD == "username"
    assert User.REQUIRED_FIELDS == ["email", "first_name", "last_name"]
    # The console is the proof: the first account must be able to sign in before email works.
    address = EmailAddress.objects.get(user=boss)
    assert address.verified and address.primary


# ---------------------------------------------------------------------- signup


def test_signing_up_needs_a_name_a_username_and_an_address(client, settings):
    settings.POSTULO_REGISTRATION_OPEN = True
    passwords = {"password1": PASSWORD, "password2": PASSWORD}
    response = client.post(reverse("account_signup"), passwords)
    assert response.status_code == 200
    form = response.context["form"]
    for field in ("first_name", "last_name", "username", "email"):
        assert field in form.errors, field
    assert not User.objects.exists()


def test_signing_up_creates_the_account_and_asks_for_the_click(client, settings):
    settings.POSTULO_REGISTRATION_OPEN = True
    response = client.post(reverse("account_signup"), SIGNUP)
    assert response.status_code == 302
    assert response.url == reverse("account_email_verification_sent")
    user = User.objects.get(username="alex.morgan")
    assert user.get_full_name() == "Alex Morgan"
    address = EmailAddress.objects.get(user=user)
    assert address.email == "alex.morgan@example.org" and not address.verified
    assert len(mail.outbox) == 1 and "alex.morgan@example.org" in mail.outbox[0].to


@pytest.mark.parametrize("username", ["admin", "root", "postulo", "Alex.Morgan"])
def test_reserved_and_taken_usernames_are_refused(client, settings, username):
    settings.POSTULO_REGISTRATION_OPEN = True
    User.objects.create_user(email="first@example.org", password=PASSWORD, username="alex.morgan")
    response = client.post(
        reverse("account_signup"), {**SIGNUP, "username": username, "email": "other@example.org"}
    )
    assert response.status_code == 200
    assert "username" in response.context["form"].errors
    assert not User.objects.filter(email="other@example.org").exists()


# ------------------------------------------------------------------- signing in


def test_either_the_username_or_the_address_signs_in(client):
    user = User.objects.create_user(
        email="alex@example.org", password=PASSWORD, username="alex", first_name="A", last_name="M"
    )
    verified(user)
    for login in ("alex", "alex@example.org", "ALEX"):
        response = client.post(reverse("account_login"), {"login": login, "password": PASSWORD})
        assert response.status_code == 302, login
        assert response.url == "/"
        client.logout()


def test_an_unproven_address_cannot_sign_in_yet(client):
    user = User.objects.create_user(email="new@example.org", password=PASSWORD, username="newbie")
    EmailAddress.objects.create(user=user, email=user.email, verified=False, primary=True)
    response = client.post(reverse("account_login"), {"login": "newbie", "password": PASSWORD})
    assert response.status_code == 302
    assert response.url == reverse("account_email_verification_sent")
    assert len(mail.outbox) == 1, "a fresh verification link is sent instead"


# ----------------------------------------------------------------- invitations


def test_an_invitation_to_an_address_is_proof_enough(client, settings, staff_user):
    settings.POSTULO_REGISTRATION_OPEN = False
    invite = Invite.objects.create(created_by=staff_user, email="alex.morgan@example.org")
    client.get(reverse("accounts:invite_accept", args=[invite.token]))
    response = client.post(reverse("account_signup"), SIGNUP)
    assert response.status_code == 302
    assert response.url != reverse("account_email_verification_sent")
    address = EmailAddress.objects.get(email="alex.morgan@example.org")
    assert address.verified, "the link went to that mailbox; that is the proof"
    assert not mail.outbox, "no second proof asked for"
    assert response.wsgi_request.user.is_authenticated


def test_an_open_invitation_still_needs_the_click(client, settings, staff_user):
    settings.POSTULO_REGISTRATION_OPEN = False
    invite = Invite.objects.create(created_by=staff_user)
    client.get(reverse("accounts:invite_accept", args=[invite.token]))
    response = client.post(reverse("account_signup"), SIGNUP)
    assert response.status_code == 302
    assert response.url == reverse("account_email_verification_sent")
    assert not EmailAddress.objects.get(email="alex.morgan@example.org").verified
    assert len(mail.outbox) == 1


# ----------------------------------------------------------------- the profile


def profile_post(**overrides):
    data = {
        "username": "applicant",
        "first_name": "Alex",
        "last_name": "Morgan",
        "headline": "",
        "phone": "",
        "location": "",
        "website": "",
        "linkedin_url": "",
        "source_repo_url": "",
        "language": "",
        "time_zone": "",
        "theme": "system",
    }
    data.update(overrides)
    return data


def test_the_profile_insists_on_a_full_name(client, user):
    client.force_login(user)
    response = client.post(reverse("accounts:profile"), profile_post(last_name=""))
    assert response.status_code == 200
    assert "last_name" in response.context["form"].errors


def test_the_profile_can_change_the_username_to_a_free_one_only(client, user, other_user):
    client.force_login(user)
    response = client.post(reverse("accounts:profile"), profile_post(username=other_user.username))
    assert response.status_code == 200
    assert "username" in response.context["form"].errors

    response = client.post(reverse("accounts:profile"), profile_post(username="admin"))
    assert "username" in response.context["form"].errors

    response = client.post(reverse("accounts:profile"), profile_post(username="Alex.Morgan"))
    assert response.status_code == 302
    user.refresh_from_db()
    assert user.username == "alex.morgan"
    assert user.display_name == "Alex Morgan"


def test_the_dashboard_asks_for_a_name_until_there_is_one(client, user):
    client.force_login(user)
    assert b"has no full name yet" in client.get(reverse("core:home")).content
    user.first_name, user.last_name = "Alex", "Morgan"
    user.save()
    assert b"has no full name yet" not in client.get(reverse("core:home")).content


# --------------------------------------------------------- existing accounts


def test_the_migration_gives_old_accounts_a_username_and_trusts_their_address():
    migration = importlib.import_module(
        "postulo.accounts.migrations.0003_username_and_verified_addresses"
    )
    # An account from before usernames existed: created around the manager, no address row.
    old = User.objects.create(email="Old.Timer@example.org", username="", password="x")
    assert old.username == ""
    assert not EmailAddress.objects.filter(user=old).exists()

    migration.give_usernames_and_trust_addresses(apps, None)

    old.refresh_from_db()
    assert old.username == "old.timer"
    address = EmailAddress.objects.get(user=old)
    assert address.email == "Old.Timer@example.org"
    assert address.verified and address.primary


def test_export_carries_the_username_and_import_takes_it_only_when_free(user, other_user):
    user.username = "alex.morgan"
    user.save()
    assert build_document(user)["account"]["username"] == "alex.morgan"
    archive_bytes = write_archive(user).getvalue()

    # Taken by the exporter, who still exists: the importing account keeps its own.
    load(other_user, zipfile.ZipFile(io.BytesIO(archive_bytes)))
    other_user.refresh_from_db()
    assert other_user.username == "someone.else"

    # Free, because the exporter has gone: the importing account takes it over.
    user.delete()
    third = User.objects.create_user(email="third@example.org", password=PASSWORD)
    load(third, zipfile.ZipFile(io.BytesIO(archive_bytes)))
    third.refresh_from_db()
    assert third.username == "alex.morgan"
