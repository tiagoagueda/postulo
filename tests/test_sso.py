"""Single sign-on through OpenID Connect: configured from the environment, native."""

import pytest
from allauth.account.models import EmailAddress
from allauth.core import context
from allauth.socialaccount.helpers import complete_social_login
from allauth.socialaccount.models import SocialAccount, SocialLogin
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.urls import reverse

from postulo.accounts import sso
from postulo.accounts.models import Invite
from postulo.accounts.social_adapter import SocialAccountAdapter, username_from_claim

pytestmark = pytest.mark.django_db

User = get_user_model()
PASSWORD = "a-fairly-long-password-42"

PROVIDER = {
    "openid_connect": {
        "APPS": [
            {
                "provider_id": "oidc",
                "name": "Authentik",
                "client_id": "postulo",
                "secret": "not-a-real-secret",
                "settings": {"server_url": "https://auth.example.org/application/o/postulo/"},
            }
        ]
    }
}


@pytest.fixture
def configured(settings):
    settings.SOCIALACCOUNT_PROVIDERS = PROVIDER
    settings.POSTULO_OIDC_AUTO_SIGNUP = False
    return settings


def a_request(rf, path="/accounts/sso/oidc/login/callback/"):
    request = rf.get(path)
    SessionMiddleware(lambda r: None).process_request(request)
    request.session.save()
    request._messages = FallbackStorage(request)
    from django.contrib.auth.models import AnonymousUser

    request.user = AnonymousUser()
    return request


def social_login(
    email: str, *, request=None, verified=True, username="", first="", last=""
) -> SocialLogin:
    """What allauth builds after the identity provider has answered: claims, parsed."""
    from allauth.socialaccount.adapter import get_adapter
    from django.test import RequestFactory

    request = request or a_request(RequestFactory())
    provider = get_adapter().get_provider(request, "oidc")
    claims = {"sub": "subject-123", "email": email, "email_verified": verified}
    if username:
        claims["preferred_username"] = username
    if first or last:
        claims.update({"given_name": first, "family_name": last, "name": f"{first} {last}"})
    return provider.sociallogin_from_response(request, claims)


# ---------------------------------------------------------------- configuration


def test_nothing_shows_until_a_provider_is_configured(client, settings):
    settings.SOCIALACCOUNT_PROVIDERS = {}
    assert sso.enabled() is False
    html = client.get(reverse("account_login")).content.decode()
    assert "sso/oidc/login" not in html


def test_a_configured_provider_is_a_link_on_the_sign_in_page(client, configured):
    assert sso.enabled() and sso.name() == "Authentik"
    html = client.get(reverse("account_login")).content.decode()
    assert "Authentik" in html
    assert sso.login_url() == "/accounts/sso/oidc/login/"
    assert sso.login_url() in html


def test_the_callback_is_the_exact_address_to_register(rf, configured):
    request = rf.get("/", HTTP_HOST="testserver")
    assert sso.callback_url(request) == "http://testserver/accounts/sso/oidc/login/callback/"


def test_settings_from_the_environment_shape_the_provider(settings):
    from postulo.config.settings import base

    assert base.SOCIALACCOUNT_LOGIN_ON_GET is True, "a POST would trip form-action 'self'"
    assert base.SOCIALACCOUNT_STORE_TOKENS is False
    assert base.SOCIALACCOUNT_EMAIL_AUTHENTICATION is True
    assert base.SOCIALACCOUNT_OPENID_CONNECT_URL_PREFIX == "sso"


# ---------------------------------------------------------------------- claims


@pytest.mark.parametrize(
    "preferred,email,expected",
    [
        ("Alex.Morgan", "alex@example.org", "alex.morgan"),
        ("alex morgan", "alex@example.org", "alex-morgan"),
        ("", "alex.morgan@example.org", "alex.morgan"),
        ("!!", "alex@example.org", "alex"),
        ("a" * 40, "x@example.org", "a" * 32),
    ],
)
def test_a_username_claim_is_made_to_fit_postulos_rules(preferred, email, expected):
    assert username_from_claim(preferred, email) == expected


def test_a_taken_username_claim_gets_a_suffix():
    User.objects.create_user(email="first@example.org", password=PASSWORD, username="alex")
    assert username_from_claim("alex", "other@example.org") == "alex2"


def test_populate_user_fills_the_identity_from_the_claims(rf, configured):
    login = social_login("alex.morgan@example.org")
    adapter = SocialAccountAdapter()
    user = adapter.populate_user(
        a_request(rf),
        login,
        {
            "email": "alex.morgan@example.org",
            "username": "AlexM",
            "first_name": "Alex",
            "last_name": "Morgan",
            "name": "Alex Morgan",
        },
    )
    assert user.username == "alexm"
    assert user.first_name == "Alex" and user.last_name == "Morgan"

    user = adapter.populate_user(
        a_request(rf), login, {"email": "pat@example.org", "name": "Pat Lee"}
    )
    assert user.username == "pat", "no preferred_username: derived from the address"
    assert user.first_name == "Pat" and user.last_name == "Lee"


# ---------------------------------------------------------------- provisioning


def test_by_default_only_existing_accounts_sign_in(rf, configured, user):
    adapter = SocialAccountAdapter()
    assert adapter.is_open_for_signup(a_request(rf), social_login("new@example.org")) is False


def test_the_provider_becomes_the_invitation_when_the_operator_says_so(rf, configured, user):
    configured.POSTULO_OIDC_AUTO_SIGNUP = True
    adapter = SocialAccountAdapter()
    assert adapter.is_open_for_signup(a_request(rf), social_login("new@example.org")) is True


def test_an_invitation_link_opens_the_door_for_sso_too(rf, configured, user):
    from postulo.accounts.adapter import INVITE_SESSION_KEY

    staff = User.objects.create_user(email="s@example.org", password=PASSWORD, is_staff=True)
    invite = Invite.objects.create(created_by=staff, email="new@example.org")
    request = a_request(rf)
    request.session[INVITE_SESSION_KEY] = invite.token
    assert SocialAccountAdapter().is_open_for_signup(request, social_login("new@example.org"))


# -------------------------------------------------------- the whole handshake


def test_a_verified_address_links_the_existing_account_and_signs_it_in(rf, configured):
    existing = User.objects.create_user(
        email="alex@example.org", password=PASSWORD, username="alex", first_name="A", last_name="M"
    )
    EmailAddress.objects.create(user=existing, email=existing.email, verified=True, primary=True)
    request = a_request(rf)

    with context.request_context(request):
        response = complete_social_login(request, social_login("alex@example.org"))

    assert response.status_code == 302
    assert request.user == existing, "signed in as the account that holds the address"
    assert SocialAccount.objects.get(user=existing).provider == "oidc"
    assert User.objects.count() == 1, "linked, not duplicated"


def test_an_address_the_provider_has_not_verified_links_to_nothing(rf, configured):
    """The gate the whole arrangement rests on, asserted rather than assumed.

    Without it, a provider that lets somebody type any address into their profile would
    let them sign in as whoever holds it here.
    """
    existing = User.objects.create_user(
        email="alex@example.org", password=PASSWORD, username="alex"
    )
    EmailAddress.objects.create(user=existing, email=existing.email, verified=True, primary=True)
    request = a_request(rf)

    with context.request_context(request):
        complete_social_login(request, social_login("alex@example.org", verified=False))

    assert not SocialAccount.objects.filter(user=existing).exists()
    assert request.user != existing


def test_an_operator_can_refuse_to_take_the_providers_word(rf, configured):
    """POSTULO_OIDC_LINK_BY_EMAIL off: sign in here once, then connect it yourself.

    On, the instance is trusting that "verified" at the provider means the person proved
    they hold the address. That is true of a Keycloak or an Authentik somebody runs, and
    not automatically true of every endpoint that speaks OpenID Connect. An operator who
    cannot answer that question about their own provider should be able to close the door.
    """
    configured.POSTULO_OIDC_LINK_BY_EMAIL = False
    existing = User.objects.create_user(
        email="alex@example.org", password=PASSWORD, username="alex"
    )
    EmailAddress.objects.create(user=existing, email=existing.email, verified=True, primary=True)
    request = a_request(rf)

    with context.request_context(request):
        complete_social_login(request, social_login("alex@example.org"))

    assert request.user != existing, "the address matched and was deliberately not used"
    assert not SocialAccount.objects.filter(user=existing).exists()
    assert User.objects.count() == 1, "and no second account was made either"


def test_the_default_is_to_link_so_nothing_changes_for_instances_already_running(settings):
    from postulo.accounts import sso

    assert sso.link_by_email() is True


def test_the_sign_in_settings_page_says_what_it_trusts(client, configured, admin_user):
    """An operator deciding whether to turn single sign-on on is standing on this page."""
    client.force_login(admin_user)
    html = client.get(reverse("server:signin")).content.decode()

    assert "data-sso-trust" in html
    assert "verified" in html
    assert "POSTULO_OIDC_LINK_BY_EMAIL" in html, "and how to close the door"


def test_the_page_says_so_when_the_door_is_closed(client, configured, admin_user):
    configured.POSTULO_OIDC_LINK_BY_EMAIL = False
    client.force_login(admin_user)
    html = client.get(reverse("server:signin")).content.decode()

    assert "connects it themselves" in html
    assert "data-sso-trust" not in html, "nothing to warn about once it is off"


def test_an_unknown_person_is_turned_away_unless_the_provider_may_create_accounts(
    rf, configured, user
):
    request = a_request(rf)
    with context.request_context(request):
        response = complete_social_login(request, social_login("new@example.org"))
    assert response.status_code == 200
    assert b"closed" in response.content.lower(), "allauth's signup-closed page"
    assert not User.objects.filter(email="new@example.org").exists()

    configured.POSTULO_OIDC_AUTO_SIGNUP = True
    request = a_request(rf)
    login = social_login(
        "new@example.org", verified=True, username="newbie", first="New", last="Person"
    )
    with context.request_context(request):
        response = complete_social_login(request, login)
    assert response.status_code == 302
    created = User.objects.get(email="new@example.org")
    assert created.username == "newbie"
    assert created.get_full_name() == "New Person"
    assert not created.has_usable_password()
    assert EmailAddress.objects.get(user=created).verified, "the provider's word is taken"
    assert request.user == created


# ------------------------------------------------------------------ the pages


def test_settings_account_offers_the_connections_page(client, user, configured):
    client.force_login(user)
    html = client.get(reverse("settings:account")).content.decode()
    assert 'data-sso="Authentik"' in html
    assert reverse("socialaccount_connections") in html
    html = client.get(reverse("socialaccount_connections")).content.decode()
    assert 'aria-label="Settings sections"' in html and html.count('aria-current="page"') == 1


def test_settings_account_stays_quiet_without_a_provider(client, user, settings):
    settings.SOCIALACCOUNT_PROVIDERS = {}
    client.force_login(user)
    assert "data-sso=" not in client.get(reverse("settings:account")).content.decode()


def test_server_settings_show_the_provider_and_the_callback(client, configured):
    admin = User.objects.create_user(
        email="admin@example.org", password=PASSWORD, username="admin-one", is_staff=True
    )
    client.force_login(admin)
    html = client.get(reverse("server:signin")).content.decode()
    assert 'data-sso="on"' in html and "Authentik" in html
    assert "/accounts/sso/oidc/login/callback/" in html
    assert "existing accounts only" in html
