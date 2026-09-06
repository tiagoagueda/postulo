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
    assert "alert-error" in html
    assert 'role="alert"' in html
    assert "are not correct" in html


def test_every_field_carries_the_shared_input_style(client):
    html = body(client, "/accounts/login/")
    assert html.count("field-input") >= 2, "the login and password fields both"
    assert "field-label" in html
    assert "btn-primary" in html, "and the button that submits them"
