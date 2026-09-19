"""The opt-in check that a newer Postulo exists (#272).

Nothing on any page could say whether the version it shows is the newest one, which is
how a correct number came to be reported as a bug. The check is off by default, asks the
configured address and nothing else, runs from the scheduler or the command and never
from a page, and a page shows only what the last check stored.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.core.cache import cache
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from postulo.core import updates

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _empty_cache():
    cache.clear()
    yield


@pytest.fixture
def admin(django_user_model):
    return django_user_model.objects.create_user(
        email="admin@example.org", username="admin", password="x", is_staff=True
    )


def a_release(monkeypatch, tag="v9.9.9"):
    asked = []

    def fetch(url):
        asked.append(url)
        return {"tag_name": tag, "html_url": f"https://example.org/releases/{tag}"}

    monkeypatch.setattr(updates, "_fetch", fetch)
    return asked


def test_it_is_off_by_default_and_then_asks_nothing(monkeypatch):
    asked = a_release(monkeypatch)
    assert not updates.enabled()
    assert not updates.due()
    answer = updates.check()
    assert asked == [], "off means no request, not a quiet one"
    assert answer["enabled"] is False and answer["latest"] == ""


def test_on_it_asks_the_configured_address_and_nothing_else(settings, monkeypatch):
    settings.POSTULO_UPDATE_CHECK = True
    settings.POSTULO_UPDATE_SOURCE = "https://example.org/releases/latest"
    asked = a_release(monkeypatch)

    answer = updates.check()

    assert asked == ["https://example.org/releases/latest"]
    assert answer["latest"] == "9.9.9" and answer["behind"] is True
    assert answer["url"].endswith("/v9.9.9") and answer["checked_at"] is not None


def test_the_tag_is_read_as_a_version():
    assert updates._version_of({"tag_name": "v0.4.0"}) == "0.4.0"
    assert updates._version_of({"tag_name": "0.4.0"}) == "0.4.0"
    assert updates._version_of({"name": "Postulo 0.4.0"}) == "Postulo 0.4.0"
    assert updates.is_behind("0.3.0", "0.4.0") and not updates.is_behind("0.4.0", "0.3.0")
    assert updates.is_behind("0.3.0", "0.3.1") and not updates.is_behind("0.3.0", "0.3.0")
    assert not updates.is_behind("0.3.0", "Postulo 0.4.0"), "unparseable is not behind"


def test_a_source_that_is_down_is_an_answer_not_an_error(settings, monkeypatch):
    settings.POSTULO_UPDATE_CHECK = True

    def fetch(url):
        raise ConnectionError("no route")

    monkeypatch.setattr(updates, "_fetch", fetch)
    answer = updates.check()
    assert answer["error"] == "no route" and answer["latest"] == ""
    assert answer["checked_at"] is not None, "when it last tried is worth showing"


def test_once_a_day_and_not_more(settings, monkeypatch):
    settings.POSTULO_UPDATE_CHECK = True
    a_release(monkeypatch)
    assert updates.due(), "never asked"
    updates.check()
    assert not updates.due(), "just asked"
    stored = cache.get(updates.CACHE_KEY)
    stored["checked_at"] = timezone.now() - dt.timedelta(days=1, minutes=1)
    cache.set(updates.CACHE_KEY, stored, timeout=None)
    assert updates.due()


def test_the_command_asks_now(settings, monkeypatch, capsys):
    settings.POSTULO_UPDATE_CHECK = True
    a_release(monkeypatch, tag="v9.9.9")
    call_command("check_for_updates")
    out = capsys.readouterr().out
    assert "9.9.9 is out" in out and updates.status()["behind"]

    settings.POSTULO_UPDATE_CHECK = False
    call_command("check_for_updates")
    assert "POSTULO_UPDATE_CHECK=true" in capsys.readouterr().out


def test_the_overview_reads_the_stored_answer_and_never_asks(client, admin, settings, monkeypatch):
    asked = a_release(monkeypatch)
    client.force_login(admin)

    html = client.get(reverse("server:overview")).content.decode()
    assert 'data-update="off"' in html and "POSTULO_UPDATE_CHECK" in html

    settings.POSTULO_UPDATE_CHECK = True
    html = client.get(reverse("server:overview")).content.decode()
    assert 'data-update="unknown"' in html and "check_for_updates" in html
    assert asked == [], "a page load asked nothing"

    updates.check()
    html = client.get(reverse("server:overview")).content.decode()
    assert 'data-update="behind"' in html and "9.9.9" in html and "newer than this instance" in html
    assert len(asked) == 1


def test_the_overview_says_when_it_is_up_to_date(client, admin, settings, monkeypatch):
    from postulo.core.context_processors import installed_version

    settings.POSTULO_UPDATE_CHECK = True
    a_release(monkeypatch, tag=f"v{installed_version()}")
    updates.check()
    client.force_login(admin)
    html = client.get(reverse("server:overview")).content.decode()
    assert 'data-update="current"' in html and "up to date" in html
