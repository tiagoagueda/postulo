"""An address nobody has proved is not a credential (#731, #730, #732).

allauth stores any address nobody holds as an *unverified* claim on whichever account asked
to add it, and its lookups by address fall back to such a claim when no account holds the
address verified. A member could therefore claim a stranger's address on their own account,
and a password reset, a sign-in code or a single sign-on login meant for that stranger would
arrive in the claimant's account instead, with the stranger carrying on inside it.

These tests make the claim the way an attacker does, through the page that adds an address,
and then check that nothing is sent to, or linked from, an address its account has not
verified. The controls matter as much: an address that *is* verified must work exactly as
before, or the fix is a lockout.
"""

from __future__ import annotations

import pytest
from allauth.account.models import EmailAddress
from django.core import mail
from django.urls import reverse

from postulo.core.models import SiteSettings
from tests.test_sso import a_request, configured, social_login  # noqa: F401 - a fixture

pytestmark = pytest.mark.django_db

PASSWORD = "a-long-enough-password-42"
VICTIM = "victim@example.org"


@pytest.fixture(autouse=True)
def _fresh_cache():
    """allauth's own limits count in the cache, and one test's must not spend another's."""
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


def pre_claim(client, account, address: str = VICTIM) -> None:
    """Add ``address`` to ``account`` from the page that does it: an unverified claim."""
    client.force_login(account)
    response = client.post(reverse("account_email"), {"email": address, "action_add": ""})
    assert response.status_code in (200, 302)
    assert EmailAddress.objects.filter(user=account, email=address, verified=False).exists()
    client.logout()
    mail.outbox.clear()


def mailed_to(address: str) -> list:
    return [message for message in mail.outbox if address in message.to]


def credentials_for(address: str) -> list:
    """What was mailed to ``address`` that lets it into an account: a reset key or a code.

    A stranger's address is told "we have no account for that address, sign up", which
    allauth sends so that the answer is the same for every address. That is not one.
    """
    return [
        message
        for message in mailed_to(address)
        if "Unknown Account" not in message.subject
        and ("/reset/key/" in message.body or "Sign-In Code" in message.subject)
    ]


# ------------------------------------------------------------ the password reset


def test_a_reset_is_not_mailed_to_an_address_only_an_unverified_claim_holds(client, mallory):
    pre_claim(client, mallory)

    response = client.post(reverse("account_reset_password"), {"email": VICTIM})

    assert response.status_code == 302, "the same answer as for any address"
    assert credentials_for(VICTIM) == [], "a key for the claimant's account went to the victim"
    assert all("mallory" not in message.body for message in mailed_to(VICTIM))


def test_a_reset_still_reaches_an_address_its_account_has_verified(client, mallory):
    response = client.post(reverse("account_reset_password"), {"email": mallory.email})

    assert response.status_code == 302
    assert len(mailed_to(mallory.email)) == 1


def test_the_verified_holder_wins_over_an_earlier_claim(client, mallory, django_user_model):
    """When the real holder does verify the address, the reset goes to their account."""
    pre_claim(client, mallory)
    victim = django_user_model.objects.create_user(
        email=VICTIM, username="victim", password=PASSWORD
    )
    EmailAddress.objects.create(user=victim, email=VICTIM, verified=True, primary=True)

    client.post(reverse("account_reset_password"), {"email": VICTIM})

    sent = mailed_to(VICTIM)
    assert len(sent) == 1
    assert "victim" in sent[0].body and "mallory" not in sent[0].body


# --------------------------------------------------------------- the sign-in code


@pytest.fixture
def code_sign_in(db):
    SiteSettings.objects.update_or_create(
        pk=1, defaults={"email_sign_in": True, "mail_failures": 0}
    )


def test_a_sign_in_code_is_not_mailed_to_an_unverified_claim(client, mallory, code_sign_in):
    pre_claim(client, mallory)

    client.post(reverse("account_request_login_code"), {"email": VICTIM})

    assert credentials_for(VICTIM) == []


def test_a_sign_in_code_still_reaches_a_verified_address(client, mallory, code_sign_in):
    client.post(reverse("account_request_login_code"), {"email": mallory.email})

    assert len(mailed_to(mallory.email)) == 1


# ------------------------------------------------------------ single sign-on (#730)


@pytest.mark.usefixtures("configured")
def test_a_provider_asserting_a_claimed_address_does_not_sign_in_the_claimant(rf, mallory):
    """The victim arrives through the identity provider with an address the provider verified.

    Mallory had listed it on her own account, unverified. The provider's word used to be
    matched to her account, its password wiped (which she undoes at her own address) and
    the provider's identity connected to it, so the victim was signed in as mallory.
    """
    from allauth.core import context
    from allauth.socialaccount.helpers import complete_social_login
    from allauth.socialaccount.models import SocialAccount

    EmailAddress.objects.create(user=mallory, email=VICTIM, verified=False, primary=False)
    request = a_request(rf)

    with context.request_context(request):
        complete_social_login(request, social_login(VICTIM))

    assert request.user != mallory, "signed in as the member who only claimed the address"
    assert not SocialAccount.objects.filter(user=mallory).exists()
    assert mallory.has_usable_password(), "and nothing was done to her account"
