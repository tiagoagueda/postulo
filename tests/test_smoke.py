"""Skeleton smoke tests: the project boots, routes resolve, the user model works."""

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse


def test_home_page_renders(client):
    response = client.get(reverse("core:home"))
    assert response.status_code == 200
    assert b"Postulo" in response.content


def test_healthz_reports_ok(client, db):
    response = client.get(reverse("core:healthz"))
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


def test_user_is_identified_by_email(user):
    assert user.email == "applicant@example.org"
    assert user.get_username() == "applicant@example.org"
    assert str(user) == "applicant@example.org"
    assert user.display_name == "applicant"


def test_creating_a_user_without_an_email_fails(db):
    with pytest.raises(ValueError, match="email address is required"):
        get_user_model().objects.create_user(email="", password="x")


def test_superuser_has_staff_and_superuser_flags(db):
    admin = get_user_model().objects.create_superuser(
        email="admin@example.org", password="not-a-real-password"
    )
    assert admin.is_staff and admin.is_superuser


def test_email_uniqueness_is_enforced(user, db):
    from django.db import IntegrityError

    with pytest.raises(IntegrityError):
        get_user_model().objects.create_user(email=user.email, password="x")


@pytest.mark.parametrize("code", ["en-gb", "fr-fr", "pt-pt"])
def test_configured_languages_are_available(code, settings):
    assert code in dict(settings.LANGUAGES)


def test_british_english_is_the_source_language(settings):
    assert settings.LANGUAGE_CODE == "en-gb"
