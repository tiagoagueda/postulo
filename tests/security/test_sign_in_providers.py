"""The rules an identity provider is held to, pinned where they hold today (#1261).

Single sign-on through OpenID Connect is in the core (#6) and stays there: #1261 asked whether
signing in could be a plugin and concluded that it cannot, because everything listed here rests
on there being one way in whose rules are known. These tests are that list, written as
behaviour, so that a change which breaks one of them is a failing test rather than a
discovery. Each was already true; none of them changes anything.

1. A provider never grants more than the local account has: no claim is a role.
2. An address links two accounts only when both sides verified it, and only when the operator
   turned linking on. Connecting a provider by hand asks for the password again.
3. A first sign-in with registration closed is turned away, unless the provider is the
   invitation, registration is open, or the person followed an invitation, which is spent once.
4. A provider sign-in is asked for the second factor unless the operator said the provider
   does that checking.
5. API tokens are their own credential: a provider sign-in neither makes nor ends one, and a
   provider going away ends none.
6. Removing the provider locks nobody out: a local way back in always remains.
7. The provider's address is the operator's: no page writes it.

Two holes in rules 2 and 5's neighbourhood are open and deliberately not pinned here, because a
test would have to assert the wrong behaviour: an `email_verified` claim sent as a *string* is
read as true (#1300), and allauth's own pages ask nothing of a provider-only account whose
sign-in is old (#1301).
"""

# `configured` is a fixture imported from tests/test_sso.py, which every test here asks for.
# ruff: noqa: F811

from __future__ import annotations

import re

import pytest
from allauth.account.models import EmailAddress
from allauth.core import context
from allauth.mfa.models import Authenticator
from allauth.socialaccount.helpers import complete_social_login
from allauth.socialaccount.models import SocialAccount
from django.contrib.auth import get_user_model
from django.core import mail
from django.urls import reverse

from postulo.accounts import recovery
from postulo.accounts.models import Invite
from postulo.api import auth as api_auth
from postulo.api.models import ApiToken
from tests.test_sso import a_request, configured, social_login  # noqa: F401 - a fixture

pytestmark = pytest.mark.django_db

User = get_user_model()
PASSWORD = "a-fairly-long-password-42"

#: Everything a provider might send that looks like a role. None of it is read.
ROLE_CLAIMS = {
    "groups": ["admin", "administrators", "postulo-admins", "staff"],
    "roles": ["admin", "superuser"],
    "is_staff": True,
    "is_superuser": True,
    "admin": True,
    "realm_access": {"roles": ["admin"]},
}


@pytest.fixture(autouse=True)
def _fresh_cache():
    """allauth's own limits count in the cache, and one test's must not spend another's."""
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()


def claims_login(email: str, *, request, subject: str = "subject-123", verified=True, **extra):
    """What allauth builds after the provider answered, with any claims the test adds."""
    from allauth.socialaccount.adapter import get_adapter

    provider = get_adapter().get_provider(request, "oidc")
    claims = {"sub": subject, "email": email, "email_verified": verified, **extra}
    return provider.sociallogin_from_response(request, claims)


def arrive(rf, email: str, **kwargs):
    """Somebody arrives through the provider; the request, signed in or not, and the answer."""
    request = a_request(rf)
    with context.request_context(request):
        response = complete_social_login(request, claims_login(email, request=request, **kwargs))
    return request, response


def local(email: str, username: str, **flags):
    person = User.objects.create_user(email=email, username=username, password=PASSWORD, **flags)
    EmailAddress.objects.create(user=person, email=email, verified=True, primary=True)
    return person


def provider_only(email: str = "sso@example.org", username: str = "sso"):
    """An account the provider made: a verified address, no password, one connection."""
    person = User.objects.create_user(email=email, username=username)
    person.set_unusable_password()
    person.save()
    EmailAddress.objects.create(user=person, email=email, verified=True, primary=True)
    SocialAccount.objects.create(user=person, provider="oidc", uid=f"subject-{username}")
    return person


def make_token(owner, raw="raw-token-for-a-script"):
    ApiToken.objects.create(owner=owner, name="script", token_hash=ApiToken.hash_token(raw))
    return raw


# ------------------------------------------------- 1. no claim is a role


def test_a_new_account_made_by_the_provider_is_given_no_role_from_its_claims(rf, configured):
    configured.POSTULO_OIDC_AUTO_SIGNUP = True
    local("first@example.org", "first", is_staff=True, is_superuser=True)

    request, response = arrive(rf, "new@example.org", preferred_username="new", **ROLE_CLAIMS)

    assert response.status_code == 302
    made = User.objects.get(email="new@example.org")
    assert request.user == made
    assert not made.is_staff and not made.is_superuser
    assert not made.groups.exists() and not made.user_permissions.exists()


def test_an_account_linked_by_address_keeps_exactly_the_role_it_had(rf, configured):
    member = local("alex@example.org", "alex")

    request, _response = arrive(rf, "alex@example.org", **ROLE_CLAIMS)

    member.refresh_from_db()
    assert request.user == member, "linked and signed in"
    assert not member.is_staff and not member.is_superuser
    assert not member.groups.exists()


def test_an_account_already_connected_gains_nothing_on_a_later_sign_in(rf, configured):
    member = provider_only()

    request, _response = arrive(
        rf, member.email, subject=SocialAccount.objects.get(user=member).uid, **ROLE_CLAIMS
    )

    member.refresh_from_db()
    assert request.user == member
    assert not member.is_staff and not member.is_superuser


def test_on_an_empty_instance_the_first_account_administers_it_whatever_door_it_used(
    rf, configured
):
    """The one role that comes with a sign-up is the first-account rule (#738), and it is
    the same for a password and a provider: whoever is first, once."""
    assert not User.objects.exists()

    _request, response = arrive(rf, "first@example.org", preferred_username="first")
    assert response.status_code == 302
    first = User.objects.get(email="first@example.org")
    assert first.is_staff and first.is_superuser

    configured.POSTULO_OIDC_AUTO_SIGNUP = True
    arrive(rf, "second@example.org", subject="subject-2", **ROLE_CLAIMS)
    second = User.objects.get(email="second@example.org")
    assert not second.is_staff and not second.is_superuser, "the rule fires once"


# ------------------------------------------- 2. linking: both sides, and only when chosen


def test_an_address_the_provider_leaves_unsaid_links_nothing(rf, configured):
    """`email_verified` absent is not a yes."""
    from allauth.socialaccount.adapter import get_adapter

    member = local("alex@example.org", "alex")
    request = a_request(rf)
    provider = get_adapter().get_provider(request, "oidc")
    login = provider.sociallogin_from_response(
        request, {"sub": "subject-123", "email": "alex@example.org"}
    )
    with context.request_context(request):
        complete_social_login(request, login)

    assert request.user != member
    assert not SocialAccount.objects.filter(user=member).exists()


def test_with_linking_off_a_verified_address_on_both_sides_still_links_nothing(rf, configured):
    configured.POSTULO_OIDC_LINK_BY_EMAIL = False
    member = local("alex@example.org", "alex")

    request, _response = arrive(rf, "alex@example.org")

    assert request.user != member
    assert not SocialAccount.objects.filter(user=member).exists()


def test_linking_a_verified_holder_leaves_its_password_and_ends_its_tokens(rf, configured):
    """allauth wipes a password only where the local address was never verified, and Postulo
    links only where it was, so the password stays. The API tokens and any live recovery link
    end all the same, since the account has a new way in (#742)."""
    member = local("alex@example.org", "alex")
    raw = make_token(member)

    request, _response = arrive(rf, "alex@example.org")

    member.refresh_from_db()
    assert request.user == member
    assert member.check_password(PASSWORD), "the password is left as it was"
    assert api_auth.lookup(raw) is None, "the token is not"


def test_connecting_a_provider_by_hand_asks_for_the_password_again(rf, configured):
    """With linking off, a person connects the provider from their own account page; allauth
    asks a person who has a password to type it again first."""
    import time

    from allauth.core.exceptions import ReauthenticationRequired
    from allauth.socialaccount.internal.flows.connect import do_connect

    from tests.recently import write_records

    member = local("alex@example.org", "alex")
    request = a_request(rf)
    request.user = member
    write_records(request.session, member, [{"method": "password", "at": time.time()}])
    login = claims_login("alex@example.org", request=request)

    with context.request_context(request), pytest.raises(ReauthenticationRequired):
        do_connect(request, login)
    assert not SocialAccount.objects.filter(user=member).exists()


# ------------------------------------- 3. the first sign-in, with registration closed


def test_a_stranger_is_turned_away_while_registration_is_closed(rf, configured):
    local("first@example.org", "first")

    request, response = arrive(rf, "stranger@example.org")

    assert response.status_code == 200 and b"closed" in response.content.lower()
    assert not User.objects.filter(email="stranger@example.org").exists()
    assert not request.user.is_authenticated


def test_an_invitation_is_one_account_on_the_provider_path_too(rf, configured):
    from postulo.accounts.adapter import INVITE_SESSION_KEY

    admin = local("admin@example.org", "admin", is_staff=True)
    invite = Invite.objects.create(created_by=admin)

    request = a_request(rf)
    request.session[INVITE_SESSION_KEY] = invite.token_fingerprint
    with context.request_context(request):
        complete_social_login(
            request, claims_login("one@example.org", request=request, subject="one")
        )
    assert User.objects.filter(email="one@example.org").exists()
    invite.refresh_from_db()
    assert invite.accepted_at is not None

    again = a_request(rf)
    again.session[INVITE_SESSION_KEY] = invite.token_fingerprint
    with context.request_context(again):
        response = complete_social_login(
            again, claims_login("two@example.org", request=again, subject="two")
        )
    assert response.status_code == 200 and b"closed" in response.content.lower()
    assert not User.objects.filter(email="two@example.org").exists(), "spent, so no second"


# ------------------------------------------------------------ 4. the second factor


def test_a_provider_sign_in_meets_the_second_factor_unless_the_operator_waived_it(rf, configured):
    member = provider_only()
    Authenticator.objects.create(
        user=member, type=Authenticator.Type.TOTP, data={"secret": "JBSWY3DPEHPK3PXP"}
    )
    subject = SocialAccount.objects.get(user=member).uid

    request, response = arrive(rf, member.email, subject=subject)
    assert "2fa/authenticate" in response["Location"]
    assert not request.user.is_authenticated, "not in until the code is typed"
    assert Authenticator.objects.filter(user=member).count() == 1

    configured.POSTULO_OIDC_IS_SECOND_FACTOR = True
    request, response = arrive(rf, member.email, subject=subject)
    assert request.user == member, "the operator said the provider does that checking"
    assert Authenticator.objects.filter(user=member).count() == 1, "and nothing was removed"


# ------------------------------------------------------------------ 5. API tokens


def test_a_provider_sign_in_neither_makes_nor_ends_a_token(rf, configured):
    member = provider_only()
    raw = make_token(member)

    request, _response = arrive(
        rf, member.email, subject=SocialAccount.objects.get(user=member).uid
    )

    assert request.user == member
    assert ApiToken.objects.filter(owner=member).count() == 1
    assert api_auth.lookup(raw) is not None


def test_the_provider_going_away_ends_no_token(configured):
    member = provider_only()
    raw = make_token(member)

    configured.SOCIALACCOUNT_PROVIDERS = {}

    assert api_auth.lookup(raw) is not None


# --------------------------------------------- 6. removing the provider locks nobody out


@pytest.fixture
def provider_gone(configured):
    """An account the provider made, and then the operator unsets every POSTULO_OIDC_*."""
    member = provider_only()
    configured.SOCIALACCOUNT_PROVIDERS = {}
    return member


def test_the_sign_in_page_stays_and_only_the_button_goes(client, provider_gone):
    html = client.get(reverse("account_login")).content.decode()

    assert 'name="password"' in html
    assert "sso/oidc/login" not in html
    assert client.get("/accounts/sso/oidc/login/").status_code == 404, "and the door answers no"


def test_nothing_the_account_holds_goes_with_the_provider(provider_gone):
    assert User.objects.filter(pk=provider_gone.pk, is_active=True).exists()
    assert SocialAccount.objects.filter(user=provider_gone).exists()
    assert EmailAddress.objects.filter(user=provider_gone, verified=True).exists()


def test_a_reset_mail_reaches_an_account_with_no_password_and_sets_its_first(client, provider_gone):
    client.post(reverse("account_reset_password"), {"email": provider_gone.email})

    [message] = mail.outbox
    assert message.to == [provider_gone.email]
    link = re.search(r"https?://[^/\s]+(/accounts/password/reset/key/\S+/)", message.body)[1]

    form = client.get(link, follow=True)
    assert form.status_code == 200
    new = "a-first-password-of-its-own-77"
    response = client.post(form.request["PATH_INFO"], {"password1": new, "password2": new})
    assert response.status_code == 302

    provider_gone.refresh_from_db()
    assert provider_gone.check_password(new)


def test_an_administrators_recovery_link_reaches_it(provider_gone):
    admin = local("admin@example.org", "admin", is_staff=True)

    _link, token = recovery.issue(provider_gone, by=admin)
    recovery.spend(recovery.find(token), "a-first-password-of-its-own-77")

    provider_gone.refresh_from_db()
    assert provider_gone.has_usable_password()


def test_the_mail_lock_never_counts_the_provider_as_a_way_back_in(provider_gone):
    """An account with only the provider needs mail, configured or not (#104)."""
    from postulo.notifications import transport

    assert transport.accounts_needing_email() >= 1
    assert all(route.name != "sso" for route in transport.all_routes())


def test_the_last_connection_of_an_account_with_no_password_cannot_be_removed(
    client, provider_gone
):
    client.force_login(provider_gone)
    account = SocialAccount.objects.get(user=provider_gone)

    response = client.post(reverse("socialaccount_connections"), {"account": account.pk})

    assert response.status_code == 200
    assert response.context["form"].errors
    assert SocialAccount.objects.filter(pk=account.pk).exists()


# ----------------------------------------------- 7. the provider's address is the operator's


def test_no_page_and_no_stored_setting_names_the_provider():
    """`POSTULO_OIDC_*` are environment settings only (#815): nothing Postulo's pages save can
    point sign-in at another provider, so the outbound requests allauth makes stay the
    operator's. (The mail's OAuth fields are mail's, not sign-in's. allauth's own `SocialApp`
    table is written by Django's admin alone, which is off unless the operator mounts it and
    then open only to the superuser flag; see *Hardening*.)"""
    from postulo.core.models import SiteSettings
    from postulo.core.server_forms import SignInForm

    words = ("oidc", "issuer", "server_url", "client_id", "client_secret", "provider")
    stored = [
        field.name
        for field in SiteSettings._meta.get_fields()
        if not field.name.startswith("email_oauth_")
    ]
    assert not [name for name in stored if any(word in name for word in words)]
    assert not [name for name in SignInForm.base_fields if any(word in name for word in words)]
