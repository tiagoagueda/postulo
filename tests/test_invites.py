"""Invitation-only registration.

A self-hosted instance holding someone's employment history should not accept strangers
by default, and an invitation addressed to one person should not be redeemable by
whoever else ends up holding the link.
"""

import re
from datetime import timedelta

import pytest
from django.db import connection
from django.urls import reverse
from django.utils import timezone

from postulo.accounts.adapter import INVITE_SESSION_KEY
from postulo.accounts.models import Invite


@pytest.fixture
def staff_user(db, django_user_model):
    return django_user_model.objects.create_user(
        email="operator@example.org", password="not-a-real-password", is_staff=True
    )


@pytest.fixture
def issued(db, staff_user):
    """An invitation and the one copy of its token, as the page that made it had them."""
    return Invite.issue(created_by=staff_user, note="A friend")


@pytest.fixture
def invite(issued):
    return issued[0]


@pytest.fixture
def token(issued):
    return issued[1]


# --------------------------------------------------------------------- model rules


def test_a_fresh_invitation_is_valid(invite):
    assert invite.is_valid()
    assert not invite.is_accepted
    assert not invite.is_expired


def test_an_expired_invitation_is_not_valid(db, staff_user):
    expired = Invite.objects.create(
        created_by=staff_user, expires_at=timezone.now() - timedelta(seconds=1)
    )
    assert expired.is_expired
    assert not expired.is_valid()


def test_an_accepted_invitation_cannot_be_reused(invite, user):
    invite.accept(user)
    invite.refresh_from_db()

    assert invite.is_accepted
    assert not invite.is_valid()


def test_an_invitation_bound_to_an_address_rejects_a_different_one(db, staff_user):
    bound = Invite.objects.create(created_by=staff_user, email="wanted@example.org")

    assert bound.is_valid("wanted@example.org")
    assert bound.is_valid("WANTED@EXAMPLE.ORG"), "matching should ignore case"
    assert not bound.is_valid("someone.else@example.org")


def test_tokens_are_unpredictable(db, staff_user):
    tokens = {Invite.issue(created_by=staff_user)[1] for _ in range(20)}
    assert len(tokens) == 20
    assert all(len(token) > 30 for token in tokens)


def test_the_token_is_not_stored(db, staff_user):
    """A copy of the database is not a set of working invitations (#232)."""
    from postulo.accounts.recovery import fingerprint

    invite, token = Invite.issue(created_by=staff_user)

    assert not hasattr(invite, "token")
    assert invite.token_fingerprint == fingerprint(token)
    assert token not in str(vars(invite))
    assert Invite.find(token) == invite
    assert Invite.find(invite.token_fingerprint) is None, "the fingerprint opens nothing"
    assert Invite.find("") is None


def test_a_row_made_without_issue_has_a_link_nobody_holds(db, staff_user):
    first = Invite.objects.create(created_by=staff_user)
    second = Invite.objects.create(created_by=staff_user)
    assert first.token_fingerprint != second.token_fingerprint
    assert first.is_valid(), "a valid invitation, that no link opens"


def test_pending_excludes_accepted_and_expired(db, staff_user, user):
    live = Invite.objects.create(created_by=staff_user)
    Invite.objects.create(created_by=staff_user, expires_at=timezone.now() - timedelta(days=1))
    Invite.objects.create(created_by=staff_user).accept(user)

    assert list(Invite.objects.pending()) == [live]


# ------------------------------------------------------------------ signup gating


def test_signup_is_closed_without_an_invitation(client, user, settings):
    # `user` exists so the instance is not empty: an empty one offers the form to
    # whoever will become its first account.
    settings.POSTULO_REGISTRATION_OPEN = False
    response = client.get(reverse("account_signup"))

    assert response.status_code == 200
    assert b'name="password1"' not in response.content, "the signup form must not be offered"


def test_signup_is_open_when_the_operator_says_so(client, db, settings):
    settings.POSTULO_REGISTRATION_OPEN = True
    response = client.get(reverse("account_signup"))

    assert response.status_code == 200
    assert b'name="password1"' in response.content


def test_following_an_invitation_opens_signup(client, invite, token, settings):
    settings.POSTULO_REGISTRATION_OPEN = False

    accept = client.get(reverse("accounts:invite_accept", args=[token]))
    assert accept.status_code == 302
    assert accept["Location"] == reverse("account_signup")
    assert client.session[INVITE_SESSION_KEY] == invite.token_fingerprint
    assert token not in str(dict(client.session)), (
        "the session holds the fingerprint, not the token"
    )

    signup = client.get(reverse("account_signup"))
    assert b'name="password1"' in signup.content


def test_an_expired_invitation_link_is_not_found(client, db, staff_user, settings):
    settings.POSTULO_REGISTRATION_OPEN = False
    _expired, token = Invite.issue(
        created_by=staff_user, expires_at=timezone.now() - timedelta(days=1)
    )

    assert client.get(reverse("accounts:invite_accept", args=[token])).status_code == 404


def test_an_unknown_token_is_not_found(client, db, settings):
    settings.POSTULO_REGISTRATION_OPEN = False
    response = client.get(reverse("accounts:invite_accept", args=["not-a-real-token"]))

    assert response.status_code == 404


def test_signing_up_through_an_invitation_spends_it(
    client, invite, token, settings, django_user_model
):
    settings.POSTULO_REGISTRATION_OPEN = False
    client.get(reverse("accounts:invite_accept", args=[token]))

    response = client.post(
        reverse("account_signup"),
        {
            "first_name": "New",
            "last_name": "Comer",
            "username": "newcomer",
            "email": "newcomer@example.org",
            "password1": "a-fairly-long-password-42",
            "password2": "a-fairly-long-password-42",
        },
    )

    assert response.status_code == 302, getattr(response, "context_data", {}).get("form")
    created = django_user_model.objects.filter(email="newcomer@example.org").first()
    assert created is not None, "the invited person should have an account"
    assert created.username == "newcomer"
    assert created.get_full_name() == "New Comer"

    invite.refresh_from_db()
    assert invite.is_accepted
    assert invite.accepted_by == created
    assert INVITE_SESSION_KEY not in client.session


def test_an_invitation_for_one_address_cannot_be_used_by_another(client, db, staff_user, settings):
    settings.POSTULO_REGISTRATION_OPEN = False
    bound, token = Invite.issue(created_by=staff_user, email="wanted@example.org")
    client.get(reverse("accounts:invite_accept", args=[token]))

    response = client.post(
        reverse("account_signup"),
        {
            "first_name": "Gate",
            "last_name": "Crasher",
            "username": "gatecrasher",
            "email": "gatecrasher@example.org",
            "password1": "a-fairly-long-password-42",
            "password2": "a-fairly-long-password-42",
        },
    )

    assert response.status_code == 200, "the form should be redisplayed with an error"
    assert "only be used with the address it was sent to" in response.content.decode()
    bound.refresh_from_db()
    assert not bound.is_accepted


# ------------------------------------------------------------------ spent once


def test_an_invitation_is_spent_once_however_many_copies_are_held(invite, user, other_user):
    """Two requests each read it unspent; only one of them gets to spend it (#544).

    Spending used to be an unconditional save, so the later of two overlapping sign-ups
    wrote its own account over the first one's and both accounts existed.
    """
    one, another = Invite.objects.get(pk=invite.pk), Invite.objects.get(pk=invite.pk)

    assert one.accept(user) is True
    assert another.accept(other_user) is False

    invite.refresh_from_db()
    assert invite.accepted_by == user
    assert another.accepted_by is None, "the copy that lost does not pretend otherwise"
    assert not another.is_accepted


def test_an_expired_invitation_cannot_be_spent(db, staff_user, user):
    expired = Invite.objects.create(
        created_by=staff_user, expires_at=timezone.now() - timedelta(seconds=1)
    )

    assert expired.accept(user) is False
    expired.refresh_from_db()
    assert expired.accepted_by is None


@pytest.fixture
def a_sign_up_that_read_it_before_it_was_spent(invite, user, monkeypatch):
    """What the second of two overlapping requests holds: a copy that still looks unspent."""
    stale = Invite.objects.get(pk=invite.pk)
    Invite.objects.get(pk=invite.pk).accept(user)
    for module in ("adapter", "signals"):
        monkeypatch.setattr(
            f"postulo.accounts.{module}.pending_invite", lambda request, **how: stale
        )
    return stale


@pytest.fixture
def a_transaction_for_each_request(monkeypatch):
    """As an instance runs. The test settings replace the database and leave this off."""
    from django.db import connections

    monkeypatch.setitem(connections.settings["default"], "ATOMIC_REQUESTS", True)


def test_a_sign_up_that_lost_the_race_gets_no_account(
    client,
    invite,
    user,
    a_sign_up_that_read_it_before_it_was_spent,
    a_transaction_for_each_request,
    settings,
    django_user_model,
):
    settings.POSTULO_REGISTRATION_OPEN = False

    response = client.post(
        reverse("account_signup"),
        {
            "first_name": "Second",
            "last_name": "Comer",
            "username": "secondcomer",
            "email": "second@example.org",
            "password1": "a-fairly-long-password-42",
            "password2": "a-fairly-long-password-42",
        },
    )

    assert response.status_code == 403
    assert not django_user_model.objects.filter(username="secondcomer").exists(), (
        "the account made before the invitation turned out to be spent is rolled back"
    )
    invite.refresh_from_db()
    assert invite.accepted_by == user, "and the list still names who really used it"


def test_a_spent_invitation_stops_nobody_when_registration_is_open(
    client, invite, user, a_sign_up_that_read_it_before_it_was_spent, settings, django_user_model
):
    """The invitation was not what let them in, so losing it is not a reason to refuse."""
    settings.POSTULO_REGISTRATION_OPEN = True

    response = client.post(
        reverse("account_signup"),
        {
            "first_name": "Second",
            "last_name": "Comer",
            "username": "secondcomer",
            "email": "second@example.org",
            "password1": "a-fairly-long-password-42",
            "password2": "a-fairly-long-password-42",
        },
    )

    assert response.status_code == 302
    assert django_user_model.objects.filter(username="secondcomer").exists()
    invite.refresh_from_db()
    assert invite.accepted_by == user


@pytest.mark.skipif(
    connection.vendor != "postgresql",
    reason="only PostgreSQL lets two requests read the same invitation at once",
)
@pytest.mark.django_db(transaction=True)
def test_a_second_sign_up_waits_for_the_first_and_then_finds_it_spent(django_user_model):
    """The row lock, on the one database where requests really overlap.

    SQLite takes its write lock when the request begins, so two sign-ups there are already
    one after the other and `select_for_update` is not even sent. On PostgreSQL each
    request reads what was committed, and without the lock both read the invitation unspent.
    """
    import threading
    from types import SimpleNamespace

    from django.db import transaction

    from postulo.accounts.adapter import pending_invite

    operator = django_user_model.objects.create_user(
        email="operator@example.org", password="not-a-real-password", is_staff=True
    )
    newcomer = django_user_model.objects.create_user(
        email="newcomer@example.org", password="not-a-real-password"
    )
    invite, _token = Invite.issue(created_by=operator)
    request = SimpleNamespace(session={INVITE_SESSION_KEY: invite.token_fingerprint})
    holding, found = threading.Event(), []

    def the_second_sign_up():
        holding.wait(10)
        try:
            with transaction.atomic():
                found.append(pending_invite(request, lock=True))
        finally:
            connection.close()

    second = threading.Thread(target=the_second_sign_up)
    second.start()
    with transaction.atomic():
        first = pending_invite(request, lock=True)
        holding.set()
        second.join(timeout=1)
        assert second.is_alive(), "the second read the invitation while the first held it"
        assert first.accept(newcomer) is True
    second.join(timeout=10)

    assert found == [None], "having waited, the second finds the invitation spent"


# ------------------------------------------------------------------- management


@pytest.mark.parametrize(
    "url_name,args",
    [("accounts:invite_list", []), ("accounts:invite_create", [])],
)
def test_invitation_management_requires_staff(client, user, url_name, args):
    client.force_login(user)
    response = client.get(reverse(url_name, args=args))

    assert response.status_code == 403


def test_invitation_management_requires_login(client, db):
    response = client.get(reverse("accounts:invite_list"))

    assert response.status_code == 302
    assert reverse("account_login") in response["Location"]


def test_staff_can_create_an_invitation_and_see_its_link_once(client, staff_user):
    client.force_login(staff_user)
    response = client.post(
        reverse("accounts:invite_create"), {"email": "friend@example.org", "note": "A friend"}
    )

    assert response.status_code == 200
    created = Invite.objects.get(email="friend@example.org")
    assert created.created_by == staff_user
    html = response.content.decode()
    assert "Copy this now. It is not shown again." in html
    link = re.search(r"http://testserver/accounts/invitation/([^/<]+)/", html)
    assert link, "the link is on the page that made it"
    assert Invite.find(link.group(1)) == created, "and it is the link that opens it"

    listed = client.get(reverse("accounts:invite_list")).content.decode()
    assert link.group(1) not in listed, "the list cannot show what is not stored"
    assert "/accounts/invitation/" not in listed


def test_staff_can_revoke_a_pending_invitation(client, staff_user, invite):
    client.force_login(staff_user)
    response = client.post(reverse("accounts:invite_revoke", args=[invite.pk]))

    assert response.status_code == 302
    assert not Invite.objects.filter(pk=invite.pk).exists()


def test_an_accepted_invitation_cannot_be_revoked(client, staff_user, invite, user):
    invite.accept(user)
    client.force_login(staff_user)

    client.post(reverse("accounts:invite_revoke", args=[invite.pk]))

    assert Invite.objects.filter(pk=invite.pk).exists(), "history should not be erasable"
