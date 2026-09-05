"""Forged requests, guessed passwords, borrowed sessions, and the headers that say no."""

import pytest
from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from postulo.applications.models import Application, Status
from postulo.jobs.models import Company, JobPosting

pytestmark = pytest.mark.django_db

PASSWORD = "a-fairly-long-password-42"


@pytest.fixture
def person(db):
    user = get_user_model().objects.create_user(
        email="alex@example.org", password=PASSWORD, username="alex", first_name="A", last_name="M"
    )
    EmailAddress.objects.create(user=user, email=user.email, verified=True, primary=True)
    return user


# ---------------------------------------------------------------------- CSRF


def test_a_form_posted_from_elsewhere_is_refused(person):
    """\"I'll make their browser submit the form for me.\" Django's token says no."""
    company = Company.objects.create(owner=person, name="Aperture")
    posting = JobPosting.objects.create(owner=person, company=company, title="Role")
    application = Application.objects.create(owner=person, posting=posting, status=Status.DRAFT)

    strict = Client(enforce_csrf_checks=True)
    strict.force_login(person)
    for url, payload in [
        (reverse("applications:status", args=[application.pk]), {"status": "applied"}),
        (reverse("accounts:delete"), {"confirm_email": person.email}),
        (reverse("core:table_settings", args=["applications"]), {"page_size": "25"}),
        (reverse("accounts:theme"), {"theme": "dark"}),
    ]:
        response = strict.post(url, payload)
        assert response.status_code == 403, url
    application.refresh_from_db()
    assert application.status == Status.DRAFT
    assert get_user_model().objects.filter(pk=person.pk).exists()


def test_the_api_takes_a_bearer_token_not_a_cookie(client, person):
    """\"They are signed in, so a page of mine can call the API through their session.\""""
    client.force_login(person)
    response = client.get("/api/v1/applications")
    assert response.status_code == 401, "a session is not a token"
    response = client.post(
        "/api/v1/captures", data='{"url": "https://x.example/"}', content_type="application/json"
    )
    assert response.status_code == 401


# ------------------------------------------------------------------ sessions


def test_signing_in_rotates_the_session_key(client, person):
    """\"I'll fix their session id before they sign in, then reuse it.\""""
    client.get(reverse("account_login"))
    before = client.session.session_key
    response = client.post(reverse("account_login"), {"login": "alex", "password": PASSWORD})
    assert response.status_code == 302
    assert client.session.session_key != before


def test_signing_out_ends_the_session(client, person):
    client.force_login(person)
    assert client.get(reverse("accounts:profile")).status_code == 200
    client.post(reverse("account_logout"))
    assert client.get(reverse("accounts:profile")).status_code == 302


# ---------------------------------------------------------------- passwords


@pytest.fixture
def clean_rate_limits():
    """allauth counts attempts in the cache, which outlives a test; start and end clean."""
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()


def test_guessing_a_password_is_slowed_down(client, person, clean_rate_limits):
    """\"I'll just try passwords until one works.\" Ten wrong ones a minute, then a wait."""
    responses = []
    for _ in range(12):
        responses.append(
            client.post(reverse("account_login"), {"login": "alex", "password": "wrong-one"})
        )
    # allauth answers the form, not a bare 429, once the limit is hit; the wrong password
    # is refused every time regardless.
    assert all(r.status_code == 200 for r in responses)
    assert any(b"Too many failed login attempts" in r.content for r in responses), (
        "the login is rate-limited per address and per account"
    )
    response = client.post(reverse("account_login"), {"login": "alex", "password": PASSWORD})
    assert response.status_code == 200, "even the right password waits once the limit is hit"


def test_a_user_record_never_holds_a_usable_secret(person):
    """A copy of the database is not a set of working credentials."""
    from postulo.api.models import ApiToken

    record, raw = ApiToken.issue(person, "laptop", scopes=("captures",))
    stored = " ".join(str(value) for value in record.__dict__.values())
    assert raw not in stored
    assert record.prefix == raw[: len(record.prefix)] and len(record.prefix) < 12
    assert person.password.startswith(("md5$", "pbkdf2", "argon2", "scrypt")), "hashed, never plain"


# ------------------------------------------------------------------ headers


def test_every_response_carries_the_defensive_headers(client, person):
    client.force_login(person)
    response = client.get(reverse("core:home"))
    assert response["X-Content-Type-Options"] == "nosniff"
    assert response["Referrer-Policy"] == "same-origin"
    assert response["X-Frame-Options"] == "DENY"


def test_the_production_settings_are_what_the_policy_says():
    """The prod settings module, imported as a document rather than run as a server."""
    import importlib
    import os

    os.environ.setdefault("POSTULO_SECRET_KEY", "x" * 64)
    os.environ.setdefault("POSTULO_ALLOWED_HOSTS", "postulo.example.org")
    prod = importlib.import_module("postulo.config.settings.prod")

    assert prod.DEBUG is False
    assert prod.SECURE_HSTS_SECONDS >= 31536000
    assert prod.SESSION_COOKIE_SECURE and prod.CSRF_COOKIE_SECURE
    assert prod.SECURE_SSL_REDIRECT
    csp = prod.SECURE_CSP
    assert csp["default-src"] == ["'none'"] and csp["script-src"] == ["'self'"]
    assert csp["frame-ancestors"] == ["'none'"] and csp["form-action"] == ["'self'"]
    assert "'unsafe-inline'" not in str(csp) and "'unsafe-eval'" not in str(csp)
    assert not any("http" in str(v) for v in csp.values()), "no third-party origin, ever"
