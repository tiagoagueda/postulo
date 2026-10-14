"""The masthead search narrows the Documents lists and the Calendar first (#848)."""

from __future__ import annotations

import datetime as dt
import re

import pytest
from django.urls import reverse
from django.utils import timezone

from postulo.applications.models import Reminder
from postulo.documents.models import CV, CoverLetter

pytestmark = pytest.mark.django_db


def masthead_search(html: str) -> str:
    return re.search(r'<form method="get"[^>]*data-site-search-form.*?</form>', html, re.S)[0]


def test_a_documents_list_gets_its_own_search_with_everything_one_button_away(client, user):
    client.force_login(user)

    html = client.get(reverse("documents:cv_list")).content.decode()

    box = masthead_search(html)
    assert 'aria-label="Search CVs and portfolios"' in box
    assert 'name="q"' in box and "data-search-everything" in box
    assert f'formaction="{reverse("core:search")}"' in box
    assert "hx-get" not in box, "plain GET: nothing to swap on these pages"


def test_the_cv_list_is_narrowed_by_q(client, user):
    CV.objects.create(owner=user, name="Backend CV", headline="Python")
    CV.objects.create(owner=user, name="Chef CV", headline="Soups")
    client.force_login(user)

    html = client.get(reverse("documents:cv_list"), {"q": "python"}).content.decode()

    assert "Backend CV" in html and "Chef CV" not in html


def test_the_letters_keep_their_kind_through_a_new_search(client, user):
    CoverLetter.objects.create(owner=user, name="Dear Aperture", subject="Hello", body="x")
    CoverLetter.objects.create(owner=user, name="Dear Black Mesa", subject="Hello", body="x")
    client.force_login(user)

    html = client.get(
        reverse("documents:letter_list"), {"q": "aperture", "kind": "cover_letter"}
    ).content.decode()

    assert "Dear Aperture" in html and "Dear Black Mesa" not in html
    assert 'name="kind" value="cover_letter"' in masthead_search(html)


def test_the_files_and_what_was_sent_have_a_search_too(client, user):
    client.force_login(user)

    uploads = client.get(reverse("documents:upload_list")).content.decode()
    sent = client.get(reverse("documents:rendered_list")).content.decode()

    assert 'aria-label="Search files"' in masthead_search(uploads)
    assert 'aria-label="Search what you sent"' in masthead_search(sent)


def test_it_never_reaches_somebody_elses_documents(client, user, other_user):
    CV.objects.create(owner=other_user, name="Theirs", headline="Secret")
    client.force_login(user)

    html = client.get(reverse("documents:cv_list"), {"q": "secret"}).content.decode()

    assert "Theirs" not in html


# ------------------------------------------------------------------ the calendar


def day_reminders(user):
    base = timezone.make_aware(dt.datetime(2026, 9, 15, 10, 0))
    Reminder.objects.create(owner=user, summary="Call Aperture", due_at=base)
    Reminder.objects.create(owner=user, summary="Pay the bill", due_at=base + dt.timedelta(days=1))


def test_the_calendar_narrows_the_period_shown_and_says_so(client, user):
    day_reminders(user)
    client.force_login(user)

    html = client.get(
        reverse("applications:calendar"), {"month": "2026-09", "q": "aperture"}
    ).content.decode()

    assert "Call Aperture" in html and "Pay the bill" not in html
    assert 'aria-label="Search the calendar"' in masthead_search(html)
    note = re.search(r"<p[^>]*data-search-note>.*?</p>", html, re.S)[0]
    assert "September 2026" in note and "aperture" in note
    # The words go with the link, not just the address: a filter written inside the
    # translated text was extracted as a placeholder and sent `?q=` out empty (#893).
    assert f'href="{reverse("core:search")}?q=aperture"' in note


def test_the_search_travels_through_the_period_the_views_and_the_days(client, user):
    day_reminders(user)
    client.force_login(user)

    html = client.get(
        reverse("applications:calendar"), {"view": "week", "on": "2026-09-15", "q": "pay"}
    ).content.decode()

    nav = re.search(r"<nav[^>]*data-period.*?</nav>", html, re.S)[0]
    assert nav.count("q=pay") >= 7, "previous, this, next and the four views"
    assert "Pay the bill" in html and "Call Aperture" not in html


def test_the_search_keeps_the_period_when_it_is_typed_again(client, user):
    client.force_login(user)

    html = client.get(
        reverse("applications:calendar"), {"view": "agenda", "on": "2026-09-15"}
    ).content.decode()

    box = masthead_search(html)
    assert 'name="view" value="agenda"' in box and 'name="on" value="2026-09-15"' in box


def test_the_file_is_what_the_page_shows(client, user):
    day_reminders(user)
    client.force_login(user)

    text = client.get(
        reverse("applications:calendar_download"), {"month": "2026-09", "q": "aperture"}
    ).content.decode()

    assert "Call Aperture" in text and "Pay the bill" not in text
