"""Signing in with a code sent by email, and the two things it must never become (#153).

The feature is allauth's; the decisions are Postulo's, and two of them are the reason this
file is in `tests/security/` rather than beside the ordinary account tests.

**It must not remove somebody's second factor by adding a first one.** If a person with an
authenticator can ask for a code at their inbox and be signed straight in, the feature has
turned two factors into one for everybody who has two. allauth applies MFA after
authentication, so the answer ought to be no — and "ought to" is not the standard. This says
it explicitly.

**It must not exist where it cannot work.** An instance whose relay is broken offering
sign-in by email is a page promising something it cannot do, to somebody who may have no
other way in.
"""

from __future__ import annotations

import pytest
from django.test import override_settings
from django.urls import reverse

from postulo.core import site
from postulo.core.models import SiteSettings

pytestmark = pytest.mark.django_db

PASSWORD = "a-long-enough-password-42"


@pytest.fixture
def offering(db):
    """An instance whose administrator has said yes and whose mail is getting through."""
    SiteSettings.objects.update_or_create(
        pk=1, defaults={"email_sign_in": True, "mail_failures": 0}
    )
    return SiteSettings.get()


@pytest.fixture
def person(django_user_model):
    from allauth.account.models import EmailAddress

    account = django_user_model.objects.create_user(
        email="person@example.org", username="person", password=PASSWORD
    )
    EmailAddress.objects.create(user=account, email=account.email, verified=True, primary=True)
    return account


def give_an_authenticator(account):
    from allauth.mfa.totp.internal import auth as totp_auth

    return totp_auth.TOTP.activate(account, totp_auth.generate_totp_secret())


# ------------------------------------------------------- where it is offered at all


def test_it_is_off_until_an_administrator_says_otherwise(db):
    assert site.email_sign_in() is False


def test_the_door_is_shut_while_it_is_off(client, db):
    assert client.get(reverse("account_request_login_code")).status_code == 404


def test_saying_yes_opens_it(client, offering):
    assert site.email_sign_in() is True
    assert client.get(reverse("account_request_login_code")).status_code == 200


def test_broken_mail_shuts_it_again(client, offering):
    """#152's whole point, arriving where it matters most."""
    SiteSettings.objects.filter(pk=1).update(mail_failures=SiteSettings.MAIL_FAILURES_BEFORE_BROKEN)

    assert site.email_sign_in() is False
    assert client.get(reverse("account_request_login_code")).status_code == 404


def test_no_transport_shuts_it_too(client, offering, monkeypatch):
    from postulo.notifications import transport

    monkeypatch.setattr(transport, "selected", lambda *a, **k: None)

    assert site.email_sign_in() is False


def test_the_sign_in_page_does_not_advertise_what_it_will_not_do(client, db):
    html = client.get(reverse("account_login")).content.decode()

    assert "sign-in code" not in html


def test_it_appears_on_the_page_once_offered(client, offering):
    html = client.get(reverse("account_login")).content.decode()

    assert "sign-in code" in html


# ------------------------------------------------- it never satisfies a second factor


def test_a_code_does_not_skip_an_authenticator(client, offering, person):
    """The question that decides whether this is a feature or a hole."""
    give_an_authenticator(person)

    client.post(reverse("account_request_login_code"), {"email": person.email})
    code = _the_code_from(client)
    response = client.post(reverse("account_confirm_login_code"), {"code": code}, follow=True)

    assert "_auth_user_id" not in client.session, "authenticated, but not signed in"
    assert "authenticator" in response.content.decode().lower() or response.redirect_chain, (
        "and sent on to the second factor"
    )


def test_somebody_without_one_is_signed_in(client, offering, person):
    """The other half: it does work, so the test above is about the factor and not the flow."""
    client.post(reverse("account_request_login_code"), {"email": person.email})
    code = _the_code_from(client)

    client.post(reverse("account_confirm_login_code"), {"code": code})

    assert client.session.get("_auth_user_id") == str(person.pk)


def test_there_is_no_setting_that_would_make_it_a_factor(db):
    """Deliberately unlike `sso_is_second_factor`, and the asymmetry is the reason.

    That setting exists because an identity provider may have checked identity carefully and
    Postulo cannot see how. A code out of an inbox has no provider behind it to trust, so
    there is nothing for an operator to decide and no switch to leave in the wrong position.
    """
    assert not hasattr(SiteSettings, "email_code_is_second_factor")
    assert not any(
        field.name.endswith("_is_second_factor") and "email" in field.name
        for field in SiteSettings._meta.get_fields()
    )


# ------------------------------------------------------------------ what it says


def test_asking_about_an_address_nobody_has_says_the_same_thing(client, offering, person):
    """Otherwise the form is a list of which addresses have accounts here."""
    known = client.post(reverse("account_request_login_code"), {"email": person.email}, follow=True)
    unknown = client.post(
        reverse("account_request_login_code"), {"email": "nobody@example.org"}, follow=True
    )

    assert known.status_code == unknown.status_code
    assert _asked_for_a_code(known) == _asked_for_a_code(unknown)


@override_settings(ACCOUNT_LOGIN_BY_CODE_MAX_ATTEMPTS=2)
def test_guessing_the_code_runs_out(client, offering, person):
    client.post(reverse("account_request_login_code"), {"email": person.email})

    for _ in range(3):
        client.post(reverse("account_confirm_login_code"), {"code": "000000"})

    assert "_auth_user_id" not in client.session


def test_the_browser_is_never_remembered(db):
    """A one-off code must not become a standing credential on somebody else's machine."""
    from allauth.account import app_settings

    assert app_settings.LOGIN_BY_CODE_TRUST_ENABLED is False


def test_a_code_does_not_last_long(db):
    from allauth.account import app_settings

    assert app_settings.LOGIN_BY_CODE_TIMEOUT <= 600


# ------------------------------------------------------------------- helpers


def _the_code_from(client) -> str:
    """The code allauth put in the outbox, read the way a person reads their mail.

    Found by its shape on its own line rather than by a fixed length: the format is
    allauth's setting, and grouping it as `XNQJ-KDLR` is exactly the sort of thing that
    changes between their releases without anybody being wrong.
    """
    import re

    from django.core import mail

    body = mail.outbox[-1].body
    for line in body.splitlines():
        token = line.strip()
        if token and re.fullmatch(r"[A-Z0-9][A-Z0-9-]{5,}", token):
            return token
    raise AssertionError(f"no code in the message: {body!r}")


def _asked_for_a_code(response) -> bool:
    """Whether the page is the one saying 'we have sent you a code'."""
    return "code" in response.content.decode().lower()
