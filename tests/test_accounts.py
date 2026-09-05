"""Profiles, preferences, and the pages that expose them."""

import pytest
from django.urls import reverse
from django.utils import timezone, translation

from postulo.accounts.models import Profile, Theme

# --------------------------------------------------------------------- profiles


def test_every_new_user_gets_a_profile(user):
    assert Profile.objects.filter(user=user).exists()


def test_the_profile_page_requires_signing_in(client, db):
    response = client.get(reverse("accounts:profile"))

    assert response.status_code == 302
    assert reverse("account_login") in response["Location"]


def test_the_profile_page_renders_for_its_owner(client, user):
    client.force_login(user)
    response = client.get(reverse("accounts:profile"))

    assert response.status_code == 200


def test_saving_the_profile_stores_details_and_name(client, user):
    client.force_login(user)
    response = client.post(
        reverse("accounts:profile"),
        {
            "username": "tiago",
            "first_name": "Tiago",
            "last_name": "Agueda",
            "headline": "Backend engineer",
            "phone": "",
            "location": "Paris, France",
            "website": "",
            "linkedin_url": "",
            "source_repo_url": "",
            "language": "pt-pt",
            "time_zone": "Europe/Paris",
            "theme": Theme.DARK,
        },
    )

    assert response.status_code == 302
    user.refresh_from_db()
    profile = user.profile
    assert user.username == "tiago"
    assert user.first_name == "Tiago"
    assert profile.headline == "Backend engineer"
    assert profile.location == "Paris, France"
    assert profile.time_zone == "Europe/Paris"


def test_the_profile_page_only_ever_edits_your_own(client, user, other_user):
    """There is no route to anyone else's profile, and posting cannot reach one."""
    client.force_login(user)
    client.post(
        reverse("accounts:profile"),
        {
            "first_name": "Changed",
            "last_name": "",
            "headline": "",
            "phone": "",
            "location": "",
            "website": "",
            "linkedin_url": "",
            "source_repo_url": "",
            "language": "",
            "time_zone": "",
            "theme": Theme.SYSTEM,
        },
    )

    other_user.refresh_from_db()
    assert other_user.first_name != "Changed"


# ------------------------------------------------------------------ preferences


def test_the_instance_default_time_zone_is_paris(settings):
    assert settings.TIME_ZONE == "Europe/Paris"


def test_a_profile_time_zone_is_activated_for_the_request(client, user):
    user.profile.time_zone = "Pacific/Auckland"
    user.profile.save()
    client.force_login(user)

    client.get(reverse("core:home"))

    assert timezone.get_current_timezone_name() == "Pacific/Auckland"


def test_an_unknown_time_zone_falls_back_instead_of_failing(client, user, settings):
    """A profile can outlive the machine's time zone database; that must not be fatal."""
    user.profile.time_zone = "Mars/Olympus_Mons"
    user.profile.save()
    client.force_login(user)

    response = client.get(reverse("core:home"))

    assert response.status_code == 200
    assert timezone.get_current_timezone_name() == settings.TIME_ZONE


def test_a_profile_language_is_activated_for_the_request(client, user):
    user.profile.language = "fr-fr"
    user.profile.save()
    client.force_login(user)

    client.get(reverse("core:home"))

    assert translation.get_language() == "fr-fr"


def test_an_anonymous_request_does_not_inherit_the_previous_visitors_time_zone(
    client, user, settings
):
    """Workers are reused; a time zone left activated must not leak to the next caller."""
    user.profile.time_zone = "Pacific/Auckland"
    user.profile.save()
    client.force_login(user)
    client.get(reverse("core:home"))

    client.logout()
    client.get(reverse("core:home"))

    assert timezone.get_current_timezone_name() == settings.TIME_ZONE


# ----------------------------------------------------------------------- theme


@pytest.mark.parametrize("theme", [Theme.LIGHT, Theme.DARK])
def test_an_explicit_theme_is_stamped_on_the_page(client, user, theme):
    user.profile.theme = theme
    user.profile.save()
    client.force_login(user)

    response = client.get(reverse("core:home"))

    assert f'data-theme="{theme.value}"'.encode() in response.content


def test_the_system_theme_stamps_nothing_and_lets_the_device_decide(client, user):
    user.profile.theme = Theme.SYSTEM
    user.profile.save()
    client.force_login(user)

    response = client.get(reverse("core:home"))

    assert b"data-theme=" not in response.content
