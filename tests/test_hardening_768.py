"""safe_next returns something redirect() can follow, or the fallback (#768)."""

from __future__ import annotations

import pytest
from django.test import RequestFactory

from postulo.core.redirects import safe_next

pytestmark = pytest.mark.django_db


def next_of(target, *, secure=False):
    return safe_next(
        RequestFactory().post("/x/", {"next": target}, secure=secure, HTTP_HOST="testserver"),
        "/home/",
    )


@pytest.mark.parametrize(
    "target",
    [
        "abc",
        "1",
        "foo.bar",
        "?x=1",
        "#frag",
        "account_login",
        "//evil.example/",
        r"/\evil.example",
        "applications:board",
        "javascript:alert(1)",
        "",
    ],
)
def test_what_is_not_a_path_on_this_site_is_the_fallback(target):
    assert next_of(target) == "/home/"


@pytest.mark.parametrize("target", ["/applications/", "/applications/?tab=notes", "/a/b/#frag"])
def test_a_path_on_this_site_is_kept(target):
    assert next_of(target) == target


def test_an_absolute_address_on_this_host_is_kept_and_another_is_not():
    assert next_of("http://testserver/applications/") == "http://testserver/applications/"
    assert next_of("http://evil.example/applications/") == "/home/"


def test_https_stays_https():
    assert next_of("http://testserver/x/", secure=True) == "/home/"
    assert next_of("https://testserver/x/", secure=True) == "https://testserver/x/"


def test_a_bare_word_no_longer_crashes_a_page_that_redirects_to_it(client, user):
    from django.urls import reverse
    from django.utils import timezone

    from postulo.applications.models import Reminder

    reminder = Reminder.objects.create(owner=user, summary="Call", due_at=timezone.now())
    client.force_login(user)

    response = client.post(
        reverse("applications:reminder_complete", args=[reminder.pk]), {"next": "abc"}
    )

    assert response.status_code == 302 and response["Location"] != "abc"
