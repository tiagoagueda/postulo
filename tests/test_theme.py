"""The theme switch in the header, and the view behind it."""

import pytest
from django.urls import reverse

from postulo.accounts.models import Profile
from postulo.core.context_processors import theme_switch

pytestmark = pytest.mark.django_db


def theme_of(user) -> str:
    return Profile.objects.get(user=user).theme


def test_switch_cycles_light_dark_system():
    assert theme_switch("light")["next"] == "dark"
    assert theme_switch("dark")["next"] == "system"
    assert theme_switch("system")["next"] == "light"
    assert theme_switch("nonsense")["current"] == "system"
    assert "Dark" in theme_switch("dark")["title"]


def test_the_switch_is_in_the_header_for_signed_in_people_only(client, user):
    response = client.get(reverse("account_login"))
    assert b"data-theme-switch" not in response.content

    client.force_login(user)
    response = client.get(reverse("core:home"))
    assert b'data-theme-switch data-current="system"' in response.content
    assert b'name="theme" value="light"' in response.content
    assert b'data-theme-icon="system"' in response.content


def test_posting_a_theme_saves_it_and_goes_back(client, user):
    client.force_login(user)
    board = reverse("applications:board")
    response = client.post(reverse("accounts:theme"), {"theme": "dark", "next": board})
    assert response.status_code == 302
    assert response.url == board
    assert theme_of(user) == "dark"

    response = client.get(reverse("core:home"))
    assert b'<html lang="en-gb" data-theme="dark">' in response.content
    assert b'data-theme-switch data-current="dark"' in response.content
    assert b'name="theme" value="system"' in response.content


def test_an_offsite_next_is_ignored(client, user):
    client.force_login(user)
    response = client.post(
        reverse("accounts:theme"), {"theme": "light", "next": "https://evil.example/"}
    )
    assert response.status_code == 302
    assert response.url == reverse("core:home")


def test_an_htmx_request_gets_the_switch_back_in_its_new_state(client, user):
    client.force_login(user)
    response = client.post(
        reverse("accounts:theme"), {"theme": "light"}, headers={"HX-Request": "true"}
    )
    assert response.status_code == 200
    assert b'data-theme-switch data-current="light"' in response.content
    assert b'name="theme" value="dark"' in response.content
    assert b"<html" not in response.content
    assert theme_of(user) == "light"


def test_only_the_three_themes_are_accepted(client, user):
    client.force_login(user)
    response = client.post(reverse("accounts:theme"), {"theme": "sepia"})
    assert response.status_code == 400
    assert theme_of(user) == "system"


def test_the_switch_needs_a_sign_in(client):
    response = client.post(reverse("accounts:theme"), {"theme": "dark"})
    assert response.status_code == 302
    assert reverse("account_login") in response.url
