"""Django's admin: off unless asked for (#116), with no login of its own (#367), and with
nobody's records in it (#368).

Found on the public test instance rather than read out of the source. `/admin/` answered
with Django's own username-and-password form, on the open internet, because
`POSTULO_ADMIN_URL` defaulted to the guessable path the comment above it said to move off.
The default is now empty and nothing is mounted; an operator who wants the admin chooses to
run it and chooses where, in the same breath.

That form was the admin's own, and what #116 did about it was to count attempts. What it
could not do was ask for a second factor: Django's login view is not allauth's, so the right
password made a full session for somebody whose account has an authenticator app. The form
is gone. The admin sends whoever is not signed in to Postulo's sign-in, which has the code,
the proven address and the limits already.

What it holds was the third finding (#368). Every user-owned model was registered with
nothing narrowing it to one owner, and appointing an administrator from *People* set
`is_superuser`, so a co-administrator read and edited every member's job search there, with
edits that skipped `change_status` and the event log. The registrations are gone and the
flag is no longer handed out; the last section walks every model rather than the ones
somebody remembered.

Mounting is decided when the URLconf is imported, so the tests that need an admin reload it
under `override_settings` rather than pretending.
"""

from __future__ import annotations

import pytest
from allauth.account.models import EmailAddress
from allauth.mfa.totp.internal.auth import TOTP
from django.apps import apps
from django.contrib import admin
from django.test import override_settings
from django.urls import NoReverseMatch, clear_url_caches, reverse

# The code is made from the clock `pinned_clock` stands still, as in test_mfa.py (#719).
from tests.test_mfa import current_code, pinned_clock  # noqa: F401

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("pinned_clock")]

#: A path an operator might choose. Nothing guesses this, which is the point of choosing.
CHOSEN = "back-office-7f3a/"

PASSWORD = "a-long-enough-password-42"
#: What an authenticator app was given when it was set up.
SECRET = "JBSWY3DPEHPK3PXP"

WITH_AN_ADMIN = override_settings(POSTULO_ADMIN_URL=CHOSEN, ROOT_URLCONF="postulo.config.urls")


@pytest.fixture
def with_an_admin():
    """Mount the admin at a chosen path, then put the URLconf back as it was."""
    import importlib

    from postulo.config import urls

    with WITH_AN_ADMIN:
        importlib.reload(urls)
        clear_url_caches()
        yield CHOSEN
    importlib.reload(urls)
    clear_url_caches()


@pytest.fixture
def superuser(django_user_model):
    return django_user_model.objects.create_user(
        email="root@example.org",
        username="root",
        password=PASSWORD,
        is_staff=True,
        is_superuser=True,
    )


# --------------------------------------------------------------- off unless asked


def test_nothing_is_published_at_the_guessable_path(client):
    """The finding, as a test: what a stranger gets from the first path they would try."""
    for path in ("/admin/", "/admin/login/", "/django-admin/"):
        assert client.get(path).status_code == 404, path


def test_the_default_mounts_no_admin_at_all(settings):
    assert settings.POSTULO_ADMIN_URL == ""
    with pytest.raises(NoReverseMatch):
        reverse("admin:index")


def test_an_operator_who_asks_for_one_gets_it_where_they_asked(client, with_an_admin):
    assert reverse("admin:index") == f"/{CHOSEN}"

    response = client.get(f"/{CHOSEN}")

    assert response.status_code == 302
    assert "login" in response["Location"]
    assert client.get("/admin/").status_code == 404, "and only where they asked"


@pytest.mark.parametrize(
    "written,mounted",
    [
        ("back-office", "back-office/"),
        ("/back-office/", "back-office/"),
        ("  back-office/  ", "back-office/"),
        ("", ""),
    ],
)
def test_the_path_an_operator_writes_is_tidied_rather_than_silently_broken(
    monkeypatch, written, mounted
):
    """A forgotten slash produced a URL nobody could reach and no error saying why.

    Through the environment, because that is where the value comes from: assigning to
    `settings.POSTULO_ADMIN_URL` would not exercise the normalisation, which happens once
    when the value is read.
    """
    from importlib import reload

    from postulo.config.settings import base

    monkeypatch.setenv("POSTULO_ADMIN_URL", written)
    reload(base)

    assert base.POSTULO_ADMIN_URL == mounted


# --------------------------------------------------------- no login of its own


@pytest.fixture
def administrator(superuser):
    """An administrator as *Hardening* asks for: a proven address and an authenticator app."""
    EmailAddress.objects.create(user=superuser, email=superuser.email, verified=True, primary=True)
    TOTP.activate(superuser, SECRET)
    return superuser


def who_is_signed_in(client) -> str | None:
    return client.session.get("_auth_user_id")


def test_a_password_alone_does_not_open_the_admin(client, with_an_admin, administrator):
    """The finding (#367): the admin's login took a password and asked for nothing else.

    It was Django's own view, so allauth's stages never ran, and the session it made was
    the one the rest of Postulo uses. Somebody with an authenticator app was one phished
    password away from every table.
    """
    response = client.post(
        f"/{CHOSEN}login/",
        {"username": "root", "password": PASSWORD, "next": f"/{CHOSEN}"},
    )

    assert who_is_signed_in(client) is None, "the right password made a session by itself"
    assert response.status_code == 302
    assert response["Location"] == f"{reverse('account_login')}?next=/{CHOSEN}"


def test_the_admin_is_reached_through_postulos_sign_in_and_its_second_step(
    client, with_an_admin, administrator
):
    """The way in that is left, walked from the admin's address to the admin's index."""
    asked = client.get(f"/{CHOSEN}", follow=True)
    assert asked.request["PATH_INFO"] == reverse("account_login")
    assert asked.status_code == 200

    response = client.post(
        reverse("account_login"),
        {"login": "root", "password": PASSWORD, "next": f"/{CHOSEN}"},
    )
    assert response["Location"] == reverse("mfa_authenticate"), "the code is asked for"
    assert who_is_signed_in(client) is None
    assert client.get(f"/{CHOSEN}").status_code == 302, "and the admin stays shut meanwhile"

    response = client.post(reverse("mfa_authenticate"), {"code": current_code(SECRET)})
    assert response["Location"] == reverse("mfa_trust")
    response = client.post(reverse("mfa_trust"), {"action": "dont_trust"})

    assert response["Location"] == f"/{CHOSEN}", "back to where they were going"
    assert client.get(f"/{CHOSEN}").status_code == 200


def test_an_administrator_cannot_become_another_from_inside(
    client, with_an_admin, administrator, django_user_model
):
    """Django's view reads a POST from somebody already signed in, and signs them in again.

    So wrapping it, which is what allauth offers for this, would have left one door: an
    administrator holding a colleague's password skips the colleague's second factor.
    """
    colleague = django_user_model.objects.create_user(
        email="second@example.org", username="second", password=PASSWORD, is_staff=True
    )
    client.force_login(colleague)

    response = client.post(f"/{CHOSEN}login/", {"username": "root", "password": PASSWORD})

    assert who_is_signed_in(client) == str(colleague.pk)
    assert response["Location"] == f"/{CHOSEN}"


def test_somebody_who_is_not_staff_is_refused_and_offered_no_form(client, with_an_admin, user):
    client.force_login(user)

    assert client.get(f"/{CHOSEN}").status_code == 302
    assert client.get(f"/{CHOSEN}login/").status_code == 403
    assert client.post(f"/{CHOSEN}login/", {"username": "root"}).status_code == 403


def test_where_to_go_afterwards_is_never_another_site(client, with_an_admin):
    response = client.get(f"/{CHOSEN}login/", {"next": "https://elsewhere.example/"})

    assert response["Location"] == f"{reverse('account_login')}?next=/{CHOSEN}"


def test_no_limit_of_the_admins_own_is_left_to_drift(with_an_admin):
    """It had one (#116), for a form that is gone. Postulo's sign-in carries allauth's."""
    from allauth.account import app_settings

    assert "admin_login" not in app_settings.RATE_LIMITS
    assert app_settings.RATE_LIMITS.get("login_failed")


# ------------------------------------------------------ one count per account

#: Five ways of naming one account at the sign-in, and any of them signs `root` in.
SPELLINGS = ["root", "Root", "ROOT", "root@example.org", "ROOT@EXAMPLE.ORG"]


def guess(client, login: str, password: str, address: str):
    """One attempt at the sign-in the admin now leads to, from an address of its own."""
    return client.post(
        reverse("account_login"), {"login": login, "password": password}, REMOTE_ADDR=address
    )


@pytest.mark.parametrize("login", SPELLINGS)
def test_every_spelling_is_the_same_account(client, administrator, login):
    """What makes the next test worth having: each of these reaches `root`."""
    response = guess(client, login, PASSWORD, "198.51.100.1")

    assert response.status_code == 302
    assert response["Location"] == reverse("mfa_authenticate"), "the password was accepted"


def test_guesses_at_one_account_are_counted_together_however_it_is_named(
    client, administrator, user
):
    """Five guesses for an account, not five for each way of naming it (#489).

    The admin's own form counted each spelling of a username apart, capitals included,
    and the address apart again. That form is gone (#367), and the sign-in it leads to
    already ignored capitals; but the username and each address still had a count each,
    so somebody guessing from many addresses had five tries per name and not per account.
    """
    for number, login in enumerate(SPELLINGS):
        answer = guess(client, login, "not-the-password", f"198.51.100.{number + 1}")
        assert "are not correct" in answer.content.decode(), login

    sixth = guess(client, "rOOt", PASSWORD, "198.51.100.99")

    assert "Too many failed login attempts" in sixth.content.decode()
    assert who_is_signed_in(client) is None, "the sixth is refused even when it is right"
    # And it is that account's count: the next person signs in from the same address.
    assert guess(client, user.email, "not-a-real-password", "198.51.100.99").status_code == 302


def test_guesses_at_a_name_nobody_has_are_still_counted(client, db):
    """No account to count against, so the name itself is counted, without its capitals."""
    for number in range(5):
        answer = guess(client, "nobody-here", "not-the-password", f"198.51.100.{number + 1}")
        assert "are not correct" in answer.content.decode()

    sixth = guess(client, "Nobody-Here", "not-the-password", "198.51.100.99")

    assert "Too many failed login attempts" in sixth.content.decode()


def test_nothing_somebody_types_lands_on_an_accounts_count(client, administrator):
    """The count is kept under the account's number, which is not a name anybody has.

    Typing that key as a name must not spend the account's five guesses for it.
    """
    for number in range(5):
        guess(client, f"account:{administrator.pk}", "anything", f"198.51.100.{number + 1}")

    response = guess(client, "root", PASSWORD, "198.51.100.99")

    assert response["Location"] == reverse("mfa_authenticate"), "root is not locked out"


def test_a_password_reset_clears_the_count_that_was_kept(rf, administrator):
    """allauth clears it by the address the reset went to, which has to be the same key."""
    from allauth.account.adapter import get_adapter

    adapter, request = get_adapter(), rf.get("/")

    by_address = adapter._get_login_attempts_cache_key(request, email="ROOT@example.org")
    by_name = adapter._get_login_attempts_cache_key(request, username="Root")

    assert by_address == by_name == f"account:{administrator.pk}"


def test_the_count_does_not_start_again_under_another_host_name(client, administrator, settings):
    """allauth keys the count on the host the request named, and an instance may have two."""
    settings.ALLOWED_HOSTS = ["testserver", "postulo.example.org"]
    for number, login in enumerate(SPELLINGS):
        guess(client, login, "not-the-password", f"198.51.100.{number + 1}")

    sixth = client.post(
        reverse("account_login"),
        {"login": "root", "password": PASSWORD},
        REMOTE_ADDR="198.51.100.99",
        HTTP_HOST="postulo.example.org",
    )

    assert "Too many failed login attempts" in sixth.content.decode()


# ------------------------------------------------------ nobody's records in it (#368)

#: The one owned model the admin keeps, and why: revoking a leaked token is the operator's
#: job. Its admin shows metadata and offers no form that could mint one.
KEPT = {"api.ApiToken"}


def an_application(owner):
    from postulo.applications.models import Application, Status
    from postulo.jobs.models import Company, JobPosting

    company = Company.objects.create(owner=owner, name="Aperture Ltd")
    posting = JobPosting.objects.create(owner=owner, company=company, title="Test Subject")
    return Application.objects.create(owner=owner, posting=posting, status=Status.APPLIED)


def records_of_people():
    """Every model whose rows belong to somebody, or hang off a row that does."""
    from postulo.applications.models import ApplicationEvent
    from postulo.core.models import OwnedModel
    from postulo.jobs.models import ListingEvent

    for model in apps.get_models():
        if issubclass(model, OwnedModel) or model in (ApplicationEvent, ListingEvent):
            yield model


def test_no_model_holding_a_persons_records_is_in_the_admin():
    """The walk the finding asked for: by what a model is, so a new one is caught too."""
    shown = [
        model._meta.label
        for model in records_of_people()
        if admin.site.is_registered(model) and model._meta.label not in KEPT
    ]

    assert shown == [], "registered with nothing narrowing it to one owner"


def test_the_one_kept_registration_can_neither_mint_nor_reword_a_token(rf, superuser):
    from postulo.api.models import ApiToken

    model_admin = admin.site._registry[ApiToken]
    request = rf.get("/")
    request.user = superuser

    assert not model_admin.has_add_permission(request)
    assert not model_admin.has_change_permission(request)
    assert model_admin.has_view_permission(request), "to see whose it is"


def test_an_appointed_administrator_is_offered_nothing_in_the_admin(
    client, with_an_admin, user, other_user
):
    """The finding as a walk: appoint through People, then try to read a stranger's rows."""
    from django.contrib.auth import get_user_model

    boss = get_user_model().objects.create_user(
        email="boss@example.org", username="boss", password=PASSWORD, is_staff=True
    )
    application = an_application(other_user)
    client.force_login(boss)

    client.post(reverse("server:person_admin", args=[user.pk]))
    user.refresh_from_db()
    assert user.is_staff and not user.is_superuser

    client.force_login(user)
    index = client.get(f"/{CHOSEN}")
    assert index.status_code == 200
    assert not index.context["app_list"], "an empty index, with no model on it"
    for path in (
        "applications/application/",
        f"applications/application/{application.pk}/change/",
        "accounts/user/",
    ):
        assert client.get(f"/{CHOSEN}{path}").status_code in (403, 404), path


def test_even_a_superuser_gets_no_page_listing_somebody_elses_applications(
    client, with_an_admin, superuser, other_user
):
    application = an_application(other_user)
    client.force_login(superuser)

    for path in (
        "applications/application/",
        f"applications/application/?owner__id__exact={other_user.pk}",
        f"applications/application/{application.pk}/change/",
        "documents/cv/",
        "jobs/jobposting/",
    ):
        assert client.get(f"/{CHOSEN}{path}").status_code == 404, path
    assert application.posting.title not in client.get(f"/{CHOSEN}").content.decode()


# ------------------------------------------------------ people added through it


def add_through_the_admin(client, email, **fields):
    data = {
        "email": email,
        "first_name": "Ada",
        "last_name": "Lovelace",
        "password1": PASSWORD,
        "password2": PASSWORD,
        "usable_password": "true",
        "profile-TOTAL_FORMS": "0",
        "profile-INITIAL_FORMS": "0",
        "profile-MIN_NUM_FORMS": "0",
        "profile-MAX_NUM_FORMS": "1",
        **fields,
    }
    return client.post(f"/{CHOSEN}accounts/user/add/", data)


def test_two_people_added_through_the_admin_each_get_a_username(
    client, with_an_admin, superuser, django_user_model
):
    """The finding (#427): the add form never asked for a username, so the first person had
    '' and the second ended in an IntegrityError and a 500."""
    client.force_login(superuser)

    first = add_through_the_admin(client, "a@example.org", username="ada")
    second = add_through_the_admin(client, "b@example.org", username="grace")

    assert first.status_code == 302, first.context["errors"]
    assert second.status_code == 302
    names = set(
        django_user_model.objects.filter(email__in=["a@example.org", "b@example.org"]).values_list(
            "username", flat=True
        )
    )
    assert names == {"ada", "grace"}


def test_the_admin_add_form_refuses_a_blacklisted_or_taken_username(
    client, with_an_admin, superuser, django_user_model
):
    client.force_login(superuser)

    for refused in ("admin", "root"):
        response = add_through_the_admin(client, "c@example.org", username=refused)
        assert response.status_code == 200, refused
        assert "username" in response.context["adminform"].form.errors, refused
    assert not django_user_model.objects.filter(email="c@example.org").exists()


def test_the_admin_add_form_asks_for_the_username_and_the_name(with_an_admin, rf, superuser):
    request = rf.get("/")
    request.user = superuser
    fields = admin.site._registry[type(superuser)].get_form(request).base_fields

    assert {"username", "first_name", "last_name"} <= set(fields)
