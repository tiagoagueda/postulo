"""The pages allauth renders, checked for being inside Postulo's layout and design.

Postulo overrides the four `account/base_*.html` templates that allauth's own pages extend.
Those overrides used to wrap their content in a block called `content_body`, and every
allauth page fills `content` — so a child replaced the wrapper outright and none of these
pages ever had a card, a heading size, a styled control or a coloured error. Thirty-one
page templates inherit one of those bases.

The axe suite visited the sign-in page in both themes throughout and reported no
violations, because everything a machine can check was correct: the labels were
associated, black on white has plenty of contrast, and a button was a button. So the check
here is a different one — that Postulo's own stylesheet actually reaches these pages — and
it is worth having precisely because the accessibility suite cannot notice its absence.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db

#: A class that exists only in Postulo's stylesheet. If it is on the page, the layout
#: wrapped it; if it is not, allauth's bare markup is being served.
CARD = 'class="card'

#: allauth's untouched defaults. `errorlist` comes from `{{ form.as_p }}`, which is what
#: its `fields` element renders when nothing overrides it.
ALLAUTH_DEFAULTS = ('class="errorlist', "errorlist nonfield")


def body(client, path):
    response = client.get(path, follow=True)
    assert response.status_code == 200, f"{path} answered {response.status_code}"
    return response.content.decode()


ANONYMOUS_PAGES = [
    "/accounts/login/",
    "/accounts/password/reset/",
    "/accounts/password/reset/done/",
]

SIGNED_IN_PAGES = [
    "/accounts/email/",
    "/accounts/2fa/",
    "/accounts/2fa/totp/activate/",
    "/accounts/password/change/",
    "/accounts/social/connections/",
    "/accounts/reauthenticate/",
]


@pytest.mark.parametrize("path", ANONYMOUS_PAGES)
def test_an_entrance_page_is_inside_postulos_layout(client, path):
    assert CARD in body(client, path), f"{path} is not wrapped by account/base_entrance.html"


@pytest.mark.parametrize("path", SIGNED_IN_PAGES)
def test_a_signed_in_allauth_page_is_inside_postulos_layout(client, user, path):
    client.force_login(user)
    assert CARD in body(client, path), f"{path} is not wrapped by its Postulo base"


@pytest.mark.parametrize("path", ANONYMOUS_PAGES + SIGNED_IN_PAGES)
def test_no_allauth_page_falls_back_to_the_default_markup(client, user, path):
    client.force_login(user)
    html = body(client, path)
    for marker in ALLAUTH_DEFAULTS:
        assert marker not in html, f"{path} is rendering allauth's own {marker!r}"


def test_the_settings_pages_allauth_renders_keep_the_sidebar(client, user):
    """They extend settings/base.html, so losing the sidebar would be the same bug again."""
    client.force_login(user)
    for path in ("/accounts/email/", "/accounts/2fa/", "/accounts/password/change/"):
        html = body(client, path)
        assert reverse("settings:appearance") in html, f"{path} lost the settings sidebar"


# ------------------------------------------- the pages behind Settings → Account

#: Every allauth page *Settings → Account* leads to, by its name. The two that take a
#: passkey are the ones its *Manage* page links to.
MANAGE_PAGES = [
    "mfa_index",
    "mfa_list_webauthn",
    "mfa_add_webauthn",
    "mfa_view_recovery_codes",
    "mfa_generate_recovery_codes",
    "mfa_deactivate_totp",
]
PER_PASSKEY_PAGES = ["mfa_edit_webauthn", "mfa_remove_webauthn"]


def what_a_browser_hands_back() -> dict:
    """A registration as allauth stores one, for a passkey that signs in on its own.

    Made whole and not as a bare row. The list reads the name and whether the key is a
    passkey, and the *Add* page reads the key itself out of every passkey already there, to
    tell the browser not to register the same one twice. A row with an empty `data`, which
    other tests make only to be counted, is one neither page can draw.
    """
    import hashlib
    import json

    from cryptography.hazmat.primitives.asymmetric import ec
    from fido2.cose import ES256
    from fido2.utils import websafe_encode
    from fido2.webauthn import AttestationObject, AttestedCredentialData, AuthenticatorData

    identifier = b"a-passkey-made-for-a-test"
    key = ES256.from_cryptography_key(ec.generate_private_key(ec.SECP256R1()).public_key())
    flags = AuthenticatorData.FLAG.UP | AuthenticatorData.FLAG.UV | AuthenticatorData.FLAG.AT
    seen = AuthenticatorData.create(
        hashlib.sha256(b"testserver").digest(),
        flags,
        0,
        AttestedCredentialData.create(bytes(16), identifier, key),
    )
    asked = {"type": "webauthn.create", "challenge": "Y2hhbGxlbmdl", "origin": "http://testserver"}
    return {
        "id": websafe_encode(identifier),
        "rawId": websafe_encode(identifier),
        "type": "public-key",
        "response": {
            "clientDataJSON": websafe_encode(json.dumps(asked).encode()),
            "attestationObject": websafe_encode(bytes(AttestationObject.create("none", seen, {}))),
        },
        "clientExtensionResults": {"credProps": {"rk": True}},
    }


@pytest.fixture
def passkey(client, user):
    """Somebody who signed in a moment ago and has a passkey, an app and recovery codes.

    Through the form and not `force_login`: these pages ask for a recent sign-in, and a
    redirect to *reauthenticate*, followed to its 200, would pass for the page itself. The
    factors are added afterwards, or the form would stop to ask for one of them.
    """
    from allauth.account.models import EmailAddress
    from allauth.mfa.recovery_codes.internal.auth import RecoveryCodes
    from allauth.mfa.totp.internal.auth import TOTP
    from allauth.mfa.webauthn.internal.auth import WebAuthn

    EmailAddress.objects.create(user=user, email=user.email, verified=True, primary=True)
    signed_in = client.post(
        reverse("account_login"), {"login": user.email, "password": "not-a-real-password"}
    )
    assert signed_in.status_code == 302, "the sign-in this fixture stands on"

    made = WebAuthn.add(user, "The laptop", what_a_browser_hands_back()).instance
    TOTP.activate(user, "JBSWY3DPEHPK3PXP")
    RecoveryCodes.activate(user)
    return made


def test_the_passkey_list_opens_for_somebody_who_has_one(client, passkey):
    """*Manage* was a server error for everyone with a passkey (#424).

    allauth's list loads Django's `humanize` tags, which were not installed, so the one
    page a passkey can be renamed or removed from could not be compiled.
    """
    response = client.get(reverse("mfa_list_webauthn"))

    assert response.status_code == 200
    assert "The laptop" in response.content.decode()


@pytest.mark.parametrize("name", MANAGE_PAGES + PER_PASSKEY_PAGES)
def test_every_page_behind_settings_account_renders(client, passkey, name):
    """For somebody who has each kind of factor, so no branch of a template goes unread.

    A tag library allauth starts loading in a later release then fails here, and not for
    the person who pressed the button.
    """
    args = [passkey.pk] if name in PER_PASSKEY_PAGES else []

    response = client.get(reverse(name, args=args))

    assert response.status_code == 200, f"{name} led to {response.get('Location')}"
    assert CARD in response.content.decode(), f"{name} is not wrapped by its Postulo base"


def test_removing_a_passkey_ends_on_the_list(client, passkey):
    """It redirects to the list, so a removal that worked used to end on the error too."""
    from allauth.mfa.models import Authenticator

    response = client.post(reverse("mfa_remove_webauthn", args=[passkey.pk]), follow=True)

    assert response.status_code == 200
    assert response.request["PATH_INFO"] == reverse("mfa_list_webauthn")
    assert not Authenticator.objects.filter(pk=passkey.pk).exists()


# ------------------------------------------------------------------- the form


def test_the_sign_in_form_is_labelled_in_postulos_words(client):
    html = body(client, "/accounts/login/")
    assert "Username or email" in html, "allauth calls this field 'Login'"
    assert ">Login<" not in html
    assert "Remember Me" not in html, "title case, in a project that writes sentence case"


def test_a_wrong_password_is_shown_as_an_error_and_announced(client, user):
    """It used to be a bare list item: black text among black text, with no role.

    A person scanning the page after a failed attempt had nothing to catch their eye, and
    a screen reader was told nothing had changed.
    """
    response = client.post(
        reverse("account_login"), {"login": user.username, "password": "not-the-password"}
    )
    html = response.content.decode()
    assert 'class="alert mb-4" data-variant="error"' in html
    assert 'role="alert"' in html
    assert "are not correct" in html


def test_every_field_is_drawn_in_the_shared_shape(client):
    """Basecoat's `.field` around each control (#290), where a class on the input used to
    say it: the login and password fields both, and the button that submits them."""
    html = body(client, "/accounts/login/")
    assert html.count('class="field') >= 2, "the login and password fields both"
    assert "field-input" not in html and "field-label" not in html
    assert 'class="btn"' in html, "and the button that submits them"
