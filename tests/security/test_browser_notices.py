"""Browser notifications waiting for a tab, and who may collect them (#209).

The browser notifier leaves a notification on the instance when it cannot push it, and an open
tab collects it through one address. That address answers with somebody's reminders and
captures, so it is a boundary: it answers only the person they are for, only to a POST that
carries the CSRF token, and never to somebody signed out.

The service worker is the other address the feature adds. It is public on purpose -- it is
static code, and a browser fetches it without a session -- so what is checked about it is that
it stays exactly that: script, from the root, with nothing about anybody in it.
"""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from postulo.notifications import inbox
from postulo.notifications.base import Notification
from postulo.notifications.models import BrowserNotice

pytestmark = pytest.mark.django_db


def a_notice(title="Chase Aperture", url="/applications/1/"):
    return Notification(event="reminder_due", title=title, body="It has been a week.", url=url)


def test_a_tab_collects_only_its_own_persons_notices(client, user, other_user):
    inbox.leave(user, a_notice("Mine"))
    inbox.leave(other_user, a_notice("Theirs"))

    client.force_login(user)
    response = client.post(reverse("notifications:waiting"))

    assert response.status_code == 200
    assert [notice["title"] for notice in response.json()["notices"]] == ["Mine"]
    assert BrowserNotice.objects.for_user(other_user).get().shown_at is None, (
        "collecting mine must not mark theirs shown"
    )


def test_somebody_signed_out_gets_nothing_and_changes_nothing(client, user):
    inbox.leave(user, a_notice())

    response = client.post(reverse("notifications:waiting"))

    assert response.status_code == 401
    assert response.json() == {"notices": []}
    assert BrowserNotice.objects.get().shown_at is None


def test_collecting_is_a_post_and_needs_the_token(user):
    inbox.leave(user, a_notice())
    strict = Client(enforce_csrf_checks=True)
    strict.force_login(user)

    assert strict.get(reverse("notifications:waiting")).status_code == 405
    assert strict.post(reverse("notifications:waiting")).status_code == 403
    assert BrowserNotice.objects.get().shown_at is None, "a forged request collects nothing"


def test_a_notice_is_handed_over_once(client, user):
    inbox.leave(user, a_notice())
    client.force_login(user)

    first = client.post(reverse("notifications:waiting")).json()["notices"]
    second = client.post(reverse("notifications:waiting")).json()["notices"]

    assert len(first) == 1 and second == []
    assert client.post(reverse("notifications:waiting"))["Cache-Control"] == "no-store"


def test_the_worker_is_script_from_the_root_and_about_nobody(client, user):
    client.force_login(user)
    signed_in = client.get("/sw.js")
    signed_out = Client().get("/sw.js")

    assert reverse("notifications:worker") == "/sw.js", "a worker only controls what is below it"
    assert signed_in.status_code == signed_out.status_code == 200
    assert signed_in["Content-Type"].startswith("text/javascript")
    assert signed_in.content == signed_out.content, "nothing in it depends on who asked"
    assert b"showNotification" in signed_in.content
    assert b'addEventListener("fetch"' not in signed_in.content, (
        "a worker that intercepts requests is a second copy of the application"
    )
