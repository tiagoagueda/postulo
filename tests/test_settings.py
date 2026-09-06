"""The Settings area: sections in a sidebar, each its own page, open to plugins."""

import pytest
from django.urls import reverse

from postulo.accounts.models import Profile, Theme
from postulo.core import settings_sections
from postulo.core.settings_sections import SettingsSection

pytestmark = pytest.mark.django_db

SECTION_URLS = [
    "settings:appearance",
    "settings:locale",
    "settings:account",
    "api:token_list",
    "core:export",
    "account_email",
    "account_change_password",
]


def test_settings_needs_a_sign_in(client):
    response = client.get(reverse("settings:index"))
    assert response.status_code == 302
    assert reverse("account_login") in response.url


def test_the_index_is_the_first_section(client, user):
    client.force_login(user)
    response = client.get(reverse("settings:index"))
    assert response.status_code == 302
    assert response.url == reverse("settings:appearance")


@pytest.mark.parametrize("url_name", SECTION_URLS)
def test_every_section_shows_the_sidebar_with_itself_marked(client, user, url_name):
    client.force_login(user)
    response = client.get(reverse(url_name))
    assert response.status_code == 200
    html = response.content.decode()
    assert 'aria-label="Settings sections"' in html
    for section in settings_sections.BUILTIN:
        assert reverse(section.url_name) in html, section.slug
    assert html.count('aria-current="page"') == 1


def test_appearance_saves_the_theme(client, user):
    client.force_login(user)
    response = client.post(reverse("settings:appearance"), {"theme": Theme.DARK})
    assert response.status_code == 302
    assert Profile.objects.get(user=user).theme == Theme.DARK


def test_language_and_time_are_saved(client, user):
    client.force_login(user)
    response = client.post(
        reverse("settings:locale"), {"language": "pt-pt", "time_zone": "Europe/Lisbon"}
    )
    assert response.status_code == 302
    profile = Profile.objects.get(user=user)
    assert profile.language == "pt-pt"
    assert profile.time_zone == "Europe/Lisbon"

    response = client.post(reverse("settings:locale"), {"language": "", "time_zone": "Mars/Base"})
    assert response.status_code == 200
    assert "time_zone" in response.context["form"].errors


def test_account_lists_the_addresses_and_the_doors_to_allauth(client, user):
    from allauth.account.models import EmailAddress

    EmailAddress.objects.create(user=user, email=user.email, verified=True, primary=True)
    EmailAddress.objects.create(user=user, email="second@example.org", verified=False)
    client.force_login(user)
    html = client.get(reverse("settings:account")).content.decode()
    assert "applicant@example.org" in html and "Primary" in html
    assert "second@example.org" in html and "Awaiting verification" in html
    assert reverse("account_email") in html
    assert reverse("account_change_password") in html
    assert 'name="username"' in html


def test_your_details_no_longer_carries_preferences(client, user):
    client.force_login(user)
    html = client.get(reverse("accounts:profile")).content.decode()
    body = html[html.index("</header>") :]
    for name in ("theme", "language", "time_zone", "username"):
        assert f'name="{name}"' not in body, name
    assert reverse("settings:index") in body


def test_a_plugin_can_add_a_section(client, user):
    section = SettingsSection(
        slug="weather", label="Weather", url_name="core:home", icon="sun", order=45
    )
    settings_sections.register(section)
    try:
        ordered = [s.slug for s in settings_sections.sections()]
        assert ordered == [
            "appearance",
            "dashboard",
            "locale",
            "account",
            "connections",
            "tokens",
            "weather",
            "data",
        ]
        client.force_login(user)
        html = client.get(reverse("settings:appearance")).content.decode()
        assert "Weather" in html
    finally:
        settings_sections.unregister("weather")
    assert "weather" not in [s.slug for s in settings_sections.sections()]


def test_active_section_follows_allauth_pages_too(rf, user):
    from django.urls import resolve

    request = rf.get(reverse("account_email"))
    request.resolver_match = resolve(reverse("account_email"))
    assert settings_sections.active_section(request).slug == "account"

    request = rf.get(reverse("core:home"))
    request.resolver_match = resolve(reverse("core:home"))
    assert settings_sections.active_section(request) is None
