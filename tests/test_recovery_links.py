"""A way back into an account that does not go through the post (#103).

Email was the only route, which made mail something every instance had to keep working for
ever: the interlock refusing to switch the mail transport off while it is the last way in was
correct, and on an instance with no second route it was permanent.

The smallest honest second route needs no third party at all — an administrator issues a
single-use link and hands it over by whatever means they already trust. Its smallness is the
feature: no gateway, no vendor, no cost, and it is the answer for the instance where the
administrator is in the same room.

A link like this is a whole account in a URL, so four properties carry the weight and each is
tested here: short-lived, single-use, recorded, and shown exactly once. A fifth is what it
deliberately is *not* — a sign-in.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from postulo.accounts import recovery
from postulo.accounts.models import RecoveryLink
from postulo.notifications import transport

pytestmark = pytest.mark.django_db

PASSWORD = "a-long-enough-password-42"
NEW_PASSWORD = "another-long-enough-password-77"


@pytest.fixture
def admin(django_user_model):
    return django_user_model.objects.create_user(
        email="admin@example.org",
        username="admin",
        password=PASSWORD,
        is_staff=True,
        is_superuser=True,
    )


@pytest.fixture
def person(django_user_model):
    return django_user_model.objects.create_user(
        email="person@example.org", username="person", password=PASSWORD
    )


def issue_for(person, admin) -> str:
    _link, token = recovery.issue(person, by=admin)
    return token


# --------------------------------------------------------------- what is stored


def test_the_token_itself_is_never_stored(person, admin):
    token = issue_for(person, admin)

    stored = RecoveryLink.objects.get(person=person)
    assert token not in stored.token_fingerprint
    assert stored.token_fingerprint == recovery.fingerprint(token)
    assert len(stored.token_fingerprint) == 64


def test_the_row_records_who_did_it(person, admin):
    issue_for(person, admin)

    stored = RecoveryLink.objects.get(person=person)
    assert stored.issued_by == admin and stored.person == person


def test_two_links_are_never_alive_at_once(person, admin):
    """An administrator issuing a second one has decided the first is not being used."""
    first = issue_for(person, admin)
    issue_for(person, admin)

    assert RecoveryLink.objects.live().count() == 1
    with pytest.raises(recovery.Unusable):
        recovery.find(first)


def test_the_row_survives_the_link(person, admin):
    """Taking somebody's account back is not something to do without a trace."""
    token = issue_for(person, admin)
    recovery.spend(recovery.find(token), NEW_PASSWORD)

    stored = RecoveryLink.objects.get(person=person)
    assert stored.state == "used" and stored.used_at is not None


# ----------------------------------------------------------------- when it works


def test_a_fresh_link_opens(person, admin):
    token = issue_for(person, admin)

    assert recovery.find(token).person == person


def test_an_expired_link_does_not(person, admin):
    token = issue_for(person, admin)
    RecoveryLink.objects.update(expires_at=timezone.now() - timedelta(minutes=1))

    with pytest.raises(recovery.Unusable):
        recovery.find(token)


def test_a_used_link_does_not(person, admin):
    token = issue_for(person, admin)
    recovery.spend(recovery.find(token), NEW_PASSWORD)

    with pytest.raises(recovery.Unusable):
        recovery.find(token)


def test_a_revoked_link_does_not(person, admin):
    token = issue_for(person, admin)
    RecoveryLink.objects.update(revoked_at=timezone.now())

    with pytest.raises(recovery.Unusable):
        recovery.find(token)


def test_a_link_that_never_existed_says_the_same_thing(person, admin):
    """Which kind of no it is would say whether the link was ever real, and it is an account."""
    token = issue_for(person, admin)
    RecoveryLink.objects.update(used_at=timezone.now())

    def refusal(value: str) -> str:
        try:
            recovery.find(value)
        except recovery.Unusable as unusable:
            return str(unusable)
        raise AssertionError("that should not have opened anything")

    assert refusal(token) == refusal("not-a-token-anybody-ever-issued")


def test_an_hour_is_the_lifetime():
    assert RecoveryLink.LIFETIME == timedelta(hours=1)


# ------------------------------------------------------------ what it actually does


def test_spending_it_sets_the_password(person, admin):
    token = issue_for(person, admin)

    recovery.spend(recovery.find(token), NEW_PASSWORD)

    person.refresh_from_db()
    assert person.check_password(NEW_PASSWORD)


def test_opening_it_does_not_spend_it(client, person, admin):
    """A link-preview bot in the chat an administrator used must not burn the only way in."""
    token = issue_for(person, admin)

    client.get(reverse("accounts:recovery_open", args=[token]))

    assert RecoveryLink.objects.get(person=person).is_live


def test_the_token_does_not_appear_in_the_form_address(client, person, admin):
    """So it never reaches a Referer header or a synced browser history."""
    token = issue_for(person, admin)

    response = client.get(reverse("accounts:recovery_open", args=[token]))

    assert response.status_code == 302
    assert token not in response["Location"]


def test_the_whole_way_through(client, person, admin):
    token = issue_for(person, admin)

    client.get(reverse("accounts:recovery_open", args=[token]))
    client.post(
        reverse("accounts:recovery_set"), {"password1": NEW_PASSWORD, "password2": NEW_PASSWORD}
    )

    person.refresh_from_db()
    assert person.check_password(NEW_PASSWORD)
    assert RecoveryLink.objects.get(person=person).state == "used"


def test_it_does_not_sign_anybody_in(client, person, admin):
    """The smallest blast radius: a link that goes astray is a password change, not a session."""
    token = issue_for(person, admin)

    client.get(reverse("accounts:recovery_open", args=[token]))
    client.post(
        reverse("accounts:recovery_set"), {"password1": NEW_PASSWORD, "password2": NEW_PASSWORD}
    )

    assert "_auth_user_id" not in client.session


def test_a_bad_link_is_a_page_and_not_a_crash(client):
    response = client.get(reverse("accounts:recovery_open", args=["nonsense"]))

    assert response.status_code == 404
    assert "cannot be used" in response.content.decode()


def test_the_form_refuses_without_a_ticket(client):
    response = client.get(reverse("accounts:recovery_set"))

    assert response.status_code == 404


def test_a_revoked_link_stops_working_mid_flow(client, person, admin):
    token = issue_for(person, admin)
    client.get(reverse("accounts:recovery_open", args=[token]))
    RecoveryLink.objects.update(revoked_at=timezone.now())

    response = client.post(
        reverse("accounts:recovery_set"), {"password1": NEW_PASSWORD, "password2": NEW_PASSWORD}
    )

    assert response.status_code == 404
    person.refresh_from_db()
    assert not person.check_password(NEW_PASSWORD)


# ------------------------------------------------------------------ the page


def test_only_an_administrator_may_issue_one(client, person):
    client.force_login(person)

    response = client.post(reverse("server:person_recovery", args=[person.pk]))

    assert response.status_code in (302, 403)
    assert not RecoveryLink.objects.exists()


def test_the_link_is_shown_once(client, admin, person):
    client.force_login(admin)

    made = client.post(reverse("server:person_recovery", args=[person.pk]))
    again = client.get(reverse("server:person_recovery", args=[person.pk]))

    assert "data-recovery-link" in made.content.decode()
    assert "data-recovery-link" not in again.content.decode(), "nothing stored it"


def test_the_page_lists_what_became_of_them(client, admin, person):
    client.force_login(admin)
    client.post(reverse("server:person_recovery", args=[person.pk]))

    html = client.get(reverse("server:person_recovery", args=[person.pk])).content.decode()

    assert "Waiting to be used" in html
    assert admin.username in html or (admin.get_full_name() or "") in html


def test_an_administrator_can_stop_one_working(client, admin, person):
    client.force_login(admin)
    _link, token = recovery.issue(person, by=admin)

    client.post(reverse("server:person_recovery", args=[person.pk]), {"revoke": "1"})

    with pytest.raises(recovery.Unusable):
        recovery.find(token)


def test_the_page_says_a_link_for_yourself_is_not_a_way_back_in(client, admin):
    client.force_login(admin)

    html = client.get(reverse("server:person_recovery", args=[admin.pk])).content.decode()

    assert "data-yourself" in html


@override_settings(POSTULO_RECOVERY_RATE="2/h")
def test_issuing_is_bounded(client, admin, person):
    """Each one is a whole account in a URL, so a compromised session cannot mint fifty."""
    client.force_login(admin)
    for _ in range(2):
        client.post(reverse("server:person_recovery", args=[person.pk]))

    client.post(reverse("server:person_recovery", args=[person.pk]))

    assert RecoveryLink.objects.count() == 2


# ----------------------------------------------------------- who the route reaches


def test_no_administrator_reaches_nobody(django_user_model):
    django_user_model.objects.create_user(email="a@example.org", username="a", password=PASSWORD)

    assert recovery.accounts_no_administrator_can_reach() == 1
    assert "administrator" not in transport.recovery_routes()


def test_one_administrator_reaches_everybody_but_themselves(admin, person):
    """Issuing needs signing in, and the person who forgot their password cannot."""
    assert recovery.accounts_no_administrator_can_reach() == 1
    assert "administrator" not in transport.recovery_routes()


def test_a_lone_administrator_with_a_passkey_is_covered(admin, person):
    from allauth.mfa.models import Authenticator

    Authenticator.objects.create(
        user=admin, type=Authenticator.Type.WEBAUTHN, data={"credential": {}}
    )

    assert recovery.accounts_no_administrator_can_reach() == 0
    assert "administrator" in transport.recovery_routes()


def test_two_administrators_reach_each_other(admin, django_user_model):
    django_user_model.objects.create_user(
        email="second@example.org", username="second", password=PASSWORD, is_staff=True
    )

    assert recovery.accounts_no_administrator_can_reach() == 0
    assert "administrator" in transport.recovery_routes()


def test_the_mail_lock_opens_once_the_route_covers_everybody(admin, django_user_model):
    """The whole reason this issue existed: SMTP could never be switched off."""
    assert transport.refuse_switching_off("smtp"), "one administrator, nobody else"

    django_user_model.objects.create_user(
        email="second@example.org", username="second", password=PASSWORD, is_staff=True
    )

    assert transport.refuse_switching_off("smtp") == ""


def test_an_inactive_administrator_does_not_count(admin, django_user_model):
    second = django_user_model.objects.create_user(
        email="second@example.org", username="second", password=PASSWORD, is_staff=True
    )
    second.is_active = False
    second.save(update_fields=["is_active"])

    assert recovery.accounts_no_administrator_can_reach() == 1
