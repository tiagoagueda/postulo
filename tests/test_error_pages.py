"""The answers a person meets when something has already gone wrong (#421).

The 500 page, a refused CSRF token and a bad request are the pages nobody looks at until
they are needed. Each had been left in Django's hands: an English 500 page whose inline
style the policy refused, and two built-in fallbacks that spoke to a developer.

The views that raise live in this module, which doubles as the URL configuration, so that
nothing is added to the application only to be tested.
"""

import re

import pytest
from django.core.exceptions import SuspiciousOperation
from django.http import HttpResponse
from django.test import Client
from django.urls import path

from postulo.config.urls import urlpatterns as application_urls
from postulo.core import languages

pytestmark = pytest.mark.django_db

handler500 = "postulo.core.views.server_error"


def boom(request):
    raise RuntimeError("deliberate")


def suspicious(request):
    raise SuspiciousOperation("deliberate")


def form(request):
    return HttpResponse("saved")


urlpatterns = [
    path("boom/", boom),
    path("suspicious/", suspicious),
    path("form/", form),
    *application_urls,
]


@pytest.fixture(autouse=True)
def these_urls(settings):
    settings.ROOT_URLCONF = __name__


@pytest.fixture
def french(user):
    profile = user.profile
    profile.language = "fr-FR"
    profile.save(update_fields=["language"])
    return user


def in_french(text):
    """What the catalogue says for ``text``, which is not the English if it is working."""
    from django.utils.translation import gettext

    with languages.override("fr-FR"):
        said = gettext(text)
    assert said != text, "the French catalogue does not hold this string"
    return said


def style_sources(response):
    policy = response.headers.get("Content-Security-Policy", "")
    match = re.search(r"style-src ([^;]*)", policy)
    return match.group(1).split() if match else []


# --------------------------------------------------------------------------- 500


def test_the_500_page_speaks_the_persons_language(french):
    client = Client(raise_request_exception=False)
    client.force_login(french)
    response = client.get("/boom/")
    body = response.content.decode()

    assert response.status_code == 500
    assert '<html lang="fr-FR" dir="ltr">' in body
    assert in_french("Go back") in body


def test_the_500_page_says_a_right_to_left_language_so(user):
    profile = user.profile
    profile.language = "ar"
    profile.save(update_fields=["language"])
    client = Client(raise_request_exception=False)
    client.force_login(user)

    body = client.get("/boom/").content.decode()

    assert 'dir="rtl"' in body


def test_the_500_page_uses_no_style_the_policy_refuses():
    client = Client(raise_request_exception=False)
    response = client.get("/boom/")
    body = response.content.decode()

    assert response.status_code == 500
    sources = style_sources(response)
    assert "'self'" in sources
    for element in re.findall(r"<style\b[^>]*>", body):
        assert any(source.startswith("'nonce-") for source in sources), element
    assert 'rel="stylesheet"' in body and "error.css" in body
    assert not re.search(r"\sstyle=", body)


# --------------------------------------------------------------------------- 403


def test_a_refused_csrf_token_is_answered_in_postulos_words(french):
    client = Client(enforce_csrf_checks=True)
    client.force_login(french)
    response = client.post("/form/", {"title": "A letter"})
    body = response.content.decode()

    assert response.status_code == 403
    assert "403_csrf.html" in [t.name for t in response.templates]
    assert "logo-64" in body
    assert "More information is available with DEBUG=True." not in body
    assert "CSRF verification failed" not in body
    assert '<html lang="fr-FR"' in body
    assert in_french("Go back") in body


# --------------------------------------------------------------------------- 400


def test_a_suspicious_request_is_answered_from_the_400_page():
    response = Client().get("/suspicious/")

    assert response.status_code == 400
    assert "400.html" in [t.name for t in response.templates]
    assert "logo-64" in response.content.decode()
    assert "Bad Request (400)" not in response.content.decode()


def test_a_host_that_is_not_allowed_is_answered_from_the_400_page():
    response = Client().get("/", headers={"host": "not-allowed.example"})

    assert response.status_code == 400
    assert "400.html" in [t.name for t in response.templates]
    assert "Bad Request (400)" not in response.content.decode()
