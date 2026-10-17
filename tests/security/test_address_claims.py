"""An unproved address is dated, expires, gives way to its holder, and is not added freely (#891).

#731 stops a reset key, a sign-in code and a single sign-on login reaching an account that only
*claims* an address. The claim itself used to stay for ever and to answer the real holder's
sign-up as though an account held the address; and anyone with a session left open could put
an address of their choosing on an account. These tests hold the four remedies.
"""

from __future__ import annotations

import datetime as dt

import pytest
from allauth.account.models import EmailAddress
from django.core import mail
from django.urls import reverse
from django.utils import timezone

from postulo.accounts import addresses, claims
from postulo.accounts.models import AddressClaim
from postulo.plugins import policy
from postulo.plugins.email_addresses import EMAIL_ADDRESSES

pytestmark = pytest.mark.django_db

PASSWORD = "a-long-enough-password-42"
VICTIM = "victim@example.org"


@pytest.fixture(autouse=True)
def _fresh_cache():
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def mallory(django_user_model):
    account = django_user_model.objects.create_user(
        email="mallory@example.org", username="mallory", password=PASSWORD
    )
    EmailAddress.objects.create(user=account, email=account.email, verified=True, primary=True)
    return account


def sign_in(client, username: str = "mallory"):
    response = client.post(reverse("account_login"), {"login": username, "password": PASSWORD})
    assert response.status_code == 302


def claim(client, address: str = VICTIM):
    """Add ``address`` through the page, signed in properly and then asked again for the
    password: since allauth 65.20 a sign-in no longer stands in for that (#987)."""
    sign_in(client)
    reauthenticated = client.post(reverse("account_reauthenticate"), {"password": PASSWORD})
    assert reauthenticated.status_code == 302
    return client.post(reverse("account_email"), {"email": address, "action_add": ""})


# ----------------------------------------------------------------- it is dated


def test_a_claim_made_through_the_page_is_dated(client, mallory):
    claim(client)

    row = EmailAddress.objects.get(user=mallory, email=VICTIM)
    assert not row.verified and not row.primary
    assert abs(row.claim.claimed_at - timezone.now()) < dt.timedelta(minutes=1)


def test_the_primary_address_of_a_sign_up_waiting_for_its_link_is_not_a_claim(
    mallory, django_user_model
):
    newcomer = django_user_model.objects.create_user(
        email="pending@example.org", username="newcomer", password=PASSWORD
    )
    pending = EmailAddress.objects.create(
        user=newcomer, email="pending@example.org", verified=False, primary=True
    )
    proved = EmailAddress.objects.create(
        user=mallory, email="proved@example.org", verified=True, primary=False
    )

    assert not AddressClaim.objects.filter(address__in=[pending, proved]).exists()


# ----------------------------------------------------------------- it expires


def test_a_claim_older_than_the_confirmation_link_goes(mallory):
    old = EmailAddress.objects.create(user=mallory, email="old@example.org", verified=False)
    young = EmailAddress.objects.create(user=mallory, email="young@example.org", verified=False)
    AddressClaim.objects.filter(address=old).update(
        claimed_at=timezone.now() - claims.lifetime() - dt.timedelta(minutes=1)
    )

    assert claims.expire_unconfirmed() == 1

    assert not EmailAddress.objects.filter(pk=old.pk).exists()
    assert EmailAddress.objects.filter(pk=young.pk).exists()


def test_an_address_that_was_proved_in_time_is_never_expired(mallory):
    address = EmailAddress.objects.create(user=mallory, email="late@example.org", verified=False)
    AddressClaim.objects.filter(address=address).update(
        claimed_at=timezone.now() - dt.timedelta(days=30)
    )
    address.verified = True
    address.save()

    assert claims.expire_unconfirmed() == 0
    assert EmailAddress.objects.filter(pk=address.pk).exists()


def test_a_primary_address_still_waiting_for_its_link_is_never_expired(django_user_model):
    newcomer = django_user_model.objects.create_user(
        email="new@example.org", username="newcomer", password=PASSWORD
    )
    waiting = EmailAddress.objects.create(
        user=newcomer, email="new@example.org", verified=False, primary=True
    )
    AddressClaim.objects.create(address=waiting, claimed_at=timezone.now() - dt.timedelta(days=30))

    assert claims.expire_unconfirmed() == 0


def test_the_scheduler_expires_them_on_its_pass():
    """The pass calls it: a function nobody calls expires nothing."""
    import inspect

    from postulo.notifications.management.commands import send_due_reminders

    assert "claims.expire_unconfirmed()" in inspect.getsource(send_due_reminders.Command.one_pass)


# --------------------------------------------------- it gives way to its holder


def test_the_real_holder_can_register_an_address_somebody_claimed(client, mallory, settings):
    settings.POSTULO_REGISTRATION_OPEN = True
    claim(client)
    assert EmailAddress.objects.filter(user=mallory, email=VICTIM).exists()
    client.logout()

    response = client.post(
        reverse("account_signup"),
        {
            "first_name": "Vic",
            "last_name": "Tim",
            "username": "victim",
            "email": VICTIM,
            "password1": PASSWORD,
            "password2": PASSWORD,
        },
    )

    assert response.status_code == 302
    assert EmailAddress.objects.filter(email=VICTIM, user__username="victim").exists()
    assert not EmailAddress.objects.filter(user=mallory, email=VICTIM).exists()
    assert [m.to for m in mail.outbox if VICTIM in m.to], "and the link goes to its holder"


def test_a_proved_address_is_never_displaced_by_a_sign_up(mallory):
    EmailAddress.objects.create(user=mallory, email="second@example.org", verified=True)

    assert claims.displace("second@example.org") == 0
    assert EmailAddress.objects.filter(user=mallory, email="second@example.org").exists()


# ------------------------------------------------- it is not added without asking


def test_adding_an_address_asks_for_the_password_again(client, mallory):
    """A session that was only forced open, as a left-open laptop's might be, cannot add one."""
    client.force_login(mallory)

    response = client.post(reverse("account_email"), {"email": VICTIM, "action_add": ""})

    assert response.status_code == 302
    assert "reauthenticate" in response["Location"]
    assert not EmailAddress.objects.filter(user=mallory, email=VICTIM).exists()


@pytest.fixture
def switched_off(monkeypatch):
    real = policy.decide

    def decide(name, person):
        if name == EMAIL_ADDRESSES:
            return policy.Decision(
                on=False, offered=False, theirs=False, decided_by="administrator"
            )
        return real(name, person)

    monkeypatch.setattr(policy, "decide", decide)
    monkeypatch.setattr(addresses, "decide", decide, raising=False)


def test_the_route_refuses_an_address_where_the_feature_is_off(client, mallory, switched_off):
    response = claim(client)

    assert response.status_code == 200, "back to the form, with the reason"
    assert "keeps one address per account" in response.content.decode()
    assert not EmailAddress.objects.filter(user=mallory, email=VICTIM).exists()
