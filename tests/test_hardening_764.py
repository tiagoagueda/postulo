"""Making an API token asks for a recent sign-in, expires by default and is bounded (#764)."""

from __future__ import annotations

import pytest
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse

from postulo.api.forms import ApiTokenForm
from postulo.api.models import ApiToken
from tests.recently import signed_in_recently

pytestmark = pytest.mark.django_db


def make(client, name="Laptop"):
    return client.post(reverse("api:token_create"), {"name": name, "scopes": ["captures"]})


def test_a_session_only_forced_open_cannot_make_one(client, user):
    client.force_login(user)

    response = make(client)

    assert response.status_code == 302 and "reauthenticate" in response["Location"]
    assert not ApiToken.objects.filter(owner=user).exists()


def test_after_a_recent_sign_in_it_can(client, user):
    signed_in_recently(client, user)

    assert make(client).status_code == 200
    assert ApiToken.objects.filter(owner=user).count() == 1


def test_a_year_is_what_the_form_starts_on_and_never_is_still_a_choice():
    form = ApiTokenForm(user=None)

    assert form.fields["expires"].initial == "365"
    assert ("", "Never") in [(k, str(v)) for k, v in form.fields["expires"].choices]


@override_settings(POSTULO_API_TOKEN_RATE="2/h")
def test_only_so_many_an_hour(client, user):
    cache.clear()
    signed_in_recently(client, user)

    for number in range(3):
        make(client, f"t{number}")

    assert ApiToken.objects.filter(owner=user).count() == 2


def test_the_page_says_how_many_are_live(client, user):
    ApiToken.issue(user, "one")
    ApiToken.issue(user, "two")
    revoked, _raw = ApiToken.issue(user, "gone")
    revoked.revoke()
    client.force_login(user)

    html = client.get(reverse("api:token_list")).content.decode()

    assert "2 tokens are good for something right now" in html
