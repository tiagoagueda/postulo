"""Deleting an account: every row, every file, and never the last administrator."""

from pathlib import Path

import pytest
from allauth.account.models import EmailAddress
from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.urls import reverse

from postulo.accounts import deletion
from postulo.accounts.models import Invite, Profile
from postulo.api.models import ApiToken
from postulo.applications.models import Application, Reminder, Status
from postulo.applications.services import change_status
from postulo.core.models import OwnedModel
from postulo.documents.models import CV, RenderedDocument, UploadedDocument
from postulo.jobs.models import Company, Contact, JobPosting
from postulo.plugins.models import Connection

pytestmark = pytest.mark.django_db

PASSWORD = "a-fairly-long-password-42"


def owned_models() -> list[type]:
    return [
        model
        for model in apps.get_models()
        if issubclass(model, OwnedModel) and not model._meta.abstract
    ]


def fill(user) -> dict[str, Path]:
    """A little of everything, with real files, so a deletion has something to miss."""
    company = Company.objects.create(owner=user, name=f"Aperture {user.pk}")
    Contact.objects.create(owner=user, company=company, name="Cave")
    posting = JobPosting.objects.create(owner=user, company=company, title="Engineer")
    application = Application.objects.create(owner=user, posting=posting, status=Status.DRAFT)
    change_status(application, Status.APPLIED)
    Reminder.objects.create(
        owner=user, application=application, summary="x", due_at="2030-01-01T09:00Z"
    )
    cv = CV.objects.create(owner=user, name="CV")
    upload = UploadedDocument.objects.create(
        owner=user, title="Designed CV", file=ContentFile(b"%PDF-1.7 mine", name="cv.pdf")
    )
    sent = RenderedDocument.objects.create(
        owner=user,
        title="Sent CV",
        kind="cv",
        cv=cv,
        application=application,
        file=ContentFile(b"%PDF-1.7 sent", name="sent.pdf"),
        checksum="abc",
    )
    profile = Profile.objects.get(user=user)
    profile.avatar.save("avatar.png", ContentFile(b"\x89PNG fake"), save=True)
    ApiToken.issue(user, "Agent", scopes=("read",))
    Connection.objects.create(
        owner=user, kind="notifier", plugin="email", label="Mail", config={"to": "x@example.org"}
    )
    Invite.objects.create(created_by=user, email="friend@example.org")
    return {
        "upload": Path(upload.file.path),
        "sent": Path(sent.file.path),
        "avatar": Path(profile.avatar.path),
    }


def test_deleting_removes_every_owned_row_and_every_file(user, other_user):
    mine = fill(user)
    theirs = fill(other_user)
    for path in {**mine, **theirs}.values():
        assert path.is_file()

    report = deletion.delete_account(user)

    for model in owned_models():
        assert not model.objects.filter(owner_id=user.pk).exists(), f"{model.__name__} rows remain"
    for model in (Company, Contact, JobPosting, Application, CV, UploadedDocument, ApiToken):
        assert model.objects.filter(owner=other_user).exists(), (
            f"{model.__name__}: the other person's rows must be untouched"
        )
    assert not get_user_model().objects.filter(pk=user.pk).exists()
    assert not EmailAddress.objects.filter(user_id=user.pk).exists()
    assert (
        not Invite.objects.filter(email="friend@example.org", created_by__isnull=True)
        .pending()
        .exists()
    ), "pending invitations the person issued are revoked"

    for path in mine.values():
        assert not path.exists(), f"{path} was left on disk"
    root = Path(settings.MEDIA_ROOT)
    assert not (root / "documents" / str(user.pk)).exists()
    assert not (root / "avatars" / str(user.pk)).exists()
    for path in theirs.values():
        assert path.is_file(), "nobody else's files move"

    assert report.files_removed == 3 and report.files_missing == 0
    assert report.rows["pending invitations revoked"] == 1
    assert any("files removed" in line for line in report.as_lines())


def test_the_last_administrator_cannot_be_deleted_by_anyone(admin_only):
    with pytest.raises(deletion.LastAdministrator):
        deletion.delete_account(admin_only)
    assert get_user_model().objects.filter(pk=admin_only.pk).exists()


def test_an_administrator_with_a_peer_can_go(admin_only):
    peer = get_user_model().objects.create_user(
        email="peer@example.org", password=PASSWORD, is_staff=True, is_superuser=True
    )
    deletion.delete_account(admin_only)
    assert get_user_model().objects.filter(pk=peer.pk).exists()
    assert deletion.is_last_administrator(peer)


@pytest.fixture
def admin_only(db):
    return get_user_model().objects.create_user(
        email="admin@example.org",
        password=PASSWORD,
        username="admin-one",
        is_staff=True,
        is_superuser=True,
    )


# ------------------------------------------------------------------ the person's own


def sign_in_properly(client, user):
    """Through the login form, so allauth records the authentication for reauth."""
    EmailAddress.objects.get_or_create(
        user=user, email=user.email, defaults={"verified": True, "primary": True}
    )
    response = client.post(reverse("account_login"), {"login": user.username, "password": PASSWORD})
    assert response.status_code == 302, response.content[:300]


@pytest.fixture
def person(db):
    return get_user_model().objects.create_user(
        email="alex@example.org",
        password=PASSWORD,
        username="alex",
        first_name="Alex",
        last_name="Morgan",
    )


def test_the_page_lists_what_goes_and_offers_the_export_first(client, person):
    fill(person)
    sign_in_properly(client, person)
    response = client.get(reverse("accounts:delete"))
    assert response.status_code == 200
    html = response.content.decode()
    assert "Download the archive" in html and reverse("core:export_download") in html
    assert "data-delete-account" in html and "alex@example.org" in html
    assert html.index("Download the archive") < html.index("data-delete-account"), "export first"


def test_deleting_needs_a_recent_authentication(client, person):
    client.force_login(person)  # no authentication record: not recent enough
    response = client.get(reverse("accounts:delete"))
    assert response.status_code == 302 and "reauthenticate" in response["Location"]


def test_typing_the_wrong_address_deletes_nothing(client, person):
    sign_in_properly(client, person)
    response = client.post(reverse("accounts:delete"), {"confirm_email": "someone@example.org"})
    assert response.status_code == 200 and "not the address" in response.content.decode()
    assert get_user_model().objects.filter(pk=person.pk).exists()


def test_typing_the_address_deletes_everything_and_signs_out(client, person):
    files = fill(person)
    sign_in_properly(client, person)
    response = client.post(reverse("accounts:delete"), {"confirm_email": "ALEX@example.org"})
    assert response.status_code == 302 and response["Location"] == reverse("account_login")
    assert not get_user_model().objects.filter(pk=person.pk).exists()
    for path in files.values():
        assert not path.exists()
    assert client.get(reverse("core:home")).status_code == 200, "signed out; the landing page"
    assert "_auth_user_id" not in client.session


def test_the_last_administrator_is_told_to_appoint_another(client, admin_only):
    sign_in_properly(client, admin_only)
    page = client.get(reverse("accounts:delete")).content.decode()
    assert "data-last-administrator" in page
    response = client.post(reverse("accounts:delete"), {"confirm_email": admin_only.email})
    assert response.status_code == 200
    assert get_user_model().objects.filter(pk=admin_only.pk).exists()


def test_the_your_data_page_links_to_it(client, person):
    client.force_login(person)
    html = client.get(reverse("core:export")).content.decode()
    assert reverse("accounts:delete") in html


# ------------------------------------------------------------------ administrators


def test_an_administrator_deletes_another_account_files_included(client, admin_only, person):
    files = fill(person)
    client.force_login(admin_only)
    page = client.get(reverse("server:person_delete", args=[person.pk])).content.decode()
    assert "alex" in page and "Type the username" in page

    response = client.post(
        reverse("server:person_delete", args=[person.pk]), {"confirm_username": "wrong"}
    )
    assert response.status_code == 200 and get_user_model().objects.filter(pk=person.pk).exists()

    response = client.post(
        reverse("server:person_delete", args=[person.pk]), {"confirm_username": "Alex"}
    )
    assert response.status_code == 302
    assert not get_user_model().objects.filter(pk=person.pk).exists()
    for path in files.values():
        assert not path.exists()


def test_an_administrator_cannot_delete_themselves_or_the_last_administrator_there(
    client, admin_only
):
    client.force_login(admin_only)
    page = client.get(reverse("server:person_delete", args=[admin_only.pk])).content.decode()
    assert "from Settings" in page
    response = client.post(
        reverse("server:person_delete", args=[admin_only.pk]), {"confirm_username": "admin-one"}
    )
    assert (
        response.status_code == 302 and get_user_model().objects.filter(pk=admin_only.pk).exists()
    )

    # The last administrator is refused by the service itself, whoever asks.
    with pytest.raises(deletion.LastAdministrator):
        deletion.delete_account(admin_only)


def test_a_member_cannot_reach_the_administrators_page(client, person, other_user):
    client.force_login(person)
    response = client.get(reverse("server:person_delete", args=[other_user.pk]))
    assert response.status_code in (302, 403)


# ------------------------------------------------------------------- the command


def test_the_command_deletes_after_confirmation_and_refuses_the_last_administrator(
    person, admin_only, capsys
):
    files = fill(person)
    call_command("delete_account", "alex@example.org", "--yes")
    assert "Deleted the account alex" in capsys.readouterr().out
    assert not get_user_model().objects.filter(pk=person.pk).exists()
    assert not files["upload"].exists()

    with pytest.raises(CommandError, match="last administrator"):
        call_command("delete_account", "admin-one", "--yes")
    with pytest.raises(CommandError, match="No account"):
        call_command("delete_account", "nobody", "--yes")
