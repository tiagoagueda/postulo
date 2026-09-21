"""Recording what happened swaps in place instead of reloading the page (#257).

An application's page is where the job search actually gets recorded, and every one of
those actions was a plain form post that redirected: the answer to "I heard back" was a
fresh render of a 347-line template with its prefetches, and the reader was returned to the
top of a page they were reading the middle of.

The rule these are held to is the project's standing one (#134, #136): **the forms must
still post.** Every test here that asks for a fragment has a twin that asks without htmx
and gets the redirect, because an `hx-post` on a real form with a real action is
progressive by construction and the only way that stays true is if something checks.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from postulo.applications.models import Application, EventKind, Reminder, Status
from postulo.jobs.models import Company, JobPosting

pytestmark = pytest.mark.django_db

HTMX = {"HTTP_HX_REQUEST": "true"}


def asking_for(target: str) -> dict:
    """The headers htmx sends when a form on the detail page swaps ``target``."""
    return {**HTMX, "HTTP_HX_TARGET": target}


@pytest.fixture
def application(user):
    company = Company.objects.create(owner=user, name="Aperture Science")
    posting = JobPosting.objects.create(owner=user, company=company, title="Test Engineer")
    return Application.objects.create(owner=user, posting=posting, status=Status.APPLIED)


# ------------------------------------------------------------------ the status band


def test_a_status_change_comes_back_as_the_card_and_the_timeline(client, user, application):
    client.force_login(user)

    response = client.post(
        reverse("applications:status", args=[application.pk]),
        {"status": Status.INTERVIEWING, "note": "They called."},
        **asking_for("status-card"),
    )

    assert response.status_code == 200
    page = response.content.decode()
    assert 'id="status-card"' in page, "the card that was swapped"
    assert 'id="timeline"' in page and 'hx-swap-oob="true"' in page, "and the timeline beside it"
    assert "They called." in page, "the entry the change wrote is in the timeline it sent back"
    application.refresh_from_db()
    assert application.status == Status.INTERVIEWING


def test_the_status_card_comes_back_showing_the_new_status(client, user, application):
    """A band that still says the old status is the bug a swap invites: the page does not
    reload, so anything drawn from a stale read stays on the screen."""
    client.force_login(user)

    response = client.post(
        reverse("applications:status", args=[application.pk]),
        {"status": Status.OFFER, "note": ""},
        **asking_for("status-card"),
    )

    page = response.content.decode()
    assert f'value="{Status.OFFER}" selected' in page or f'"{Status.OFFER}" selected' in page


def test_the_list_page_still_gets_its_row(client, user, application):
    """The same view answers both, and told apart by what was asked for rather than a flag."""
    client.force_login(user)

    response = client.post(
        reverse("applications:status", args=[application.pk]),
        {"status": Status.INTERVIEWING, "note": ""},
        **{**HTMX, "HTTP_HX_TARGET": f"application-{application.pk}"},
    )

    page = response.content.decode()
    assert f'id="application-{application.pk}"' in page
    assert 'id="status-card"' not in page


def test_without_the_script_a_status_change_redirects_as_it_always_did(client, user, application):
    client.force_login(user)

    response = client.post(
        reverse("applications:status", args=[application.pk]),
        {"status": Status.INTERVIEWING, "note": ""},
    )

    assert response.status_code == 302
    assert response["Location"] == application.get_absolute_url()


# --------------------------------------------------------------- adding to the timeline


def test_an_added_entry_comes_back_in_the_timeline_with_an_empty_form(client, user, application):
    client.force_login(user)

    response = client.post(
        reverse("applications:event_create", args=[application.pk]),
        {
            "kind": EventKind.CALL,
            "summary": "Spoke to the recruiter",
            "body": "",
            "occurred_at": "2026-09-21T10:00",
        },
        **asking_for("event-form"),
    )

    assert response.status_code == 200
    page = response.content.decode()
    assert 'id="event-form"' in page and 'id="timeline"' in page
    assert application.events.filter(summary="Spoke to the recruiter").exists(), "it saved"
    timeline = page[page.index('id="timeline"') :]
    assert "Spoke to the recruiter" in timeline, "and the entry is in the timeline, not the form"
    assert "errorlist" not in page


def test_an_entry_that_will_not_save_comes_back_with_its_errors(client, user, application):
    """A swap that answered 4xx would be thrown away by htmx and the person would see
    nothing happen at all, which is worse than the page reloading."""
    client.force_login(user)

    response = client.post(
        reverse("applications:event_create", args=[application.pk]),
        {"kind": "not-a-kind", "summary": "", "body": "", "occurred_at": ""},
        **asking_for("event-form"),
    )

    assert response.status_code == 200, "htmx discards a fragment that arrives with an error"
    assert 'id="event-form"' in page_of(response)
    assert not application.events.exists()


def page_of(response) -> str:
    return response.content.decode()


def test_without_the_script_an_entry_redirects_as_it_always_did(client, user, application):
    client.force_login(user)

    response = client.post(
        reverse("applications:event_create", args=[application.pk]),
        {
            "kind": EventKind.NOTE,
            "summary": "A note",
            "body": "",
            "occurred_at": "2026-09-21T10:00",
        },
    )

    assert response.status_code == 302
    assert response["Location"] == application.get_absolute_url()


# -------------------------------------------------------------------- ticking one off


def test_a_completed_reminder_leaves_the_list_it_was_in(client, user, application):
    from django.utils import timezone

    reminder = Reminder.objects.create(
        owner=user, application=application, summary="Chase them", due_at=timezone.now()
    )
    client.force_login(user)

    response = client.post(
        reverse("applications:reminder_complete", args=[reminder.pk]),
        {"next": application.get_absolute_url()},
        **asking_for("reminders"),
    )

    assert response.status_code == 200
    page = response.content.decode()
    assert 'id="reminders"' in page
    assert "Chase them" not in page, "it is done, so it is not outstanding"
    reminder.refresh_from_db()
    assert reminder.is_done


def test_the_reminders_page_still_redirects(client, user, application):
    """The same view serves a list page that has no `#reminders` to swap."""
    from django.utils import timezone

    reminder = Reminder.objects.create(
        owner=user, application=application, summary="Chase them", due_at=timezone.now()
    )
    client.force_login(user)

    response = client.post(reverse("applications:reminder_complete", args=[reminder.pk]))

    assert response.status_code == 302


# ------------------------------------------------------------------- and on the page


def test_the_detail_page_carries_the_three_regions_and_asks_for_them(client, user, application):
    """The ids the views answer to, read off the page that names them."""
    client.force_login(user)

    page = client.get(application.get_absolute_url()).content.decode()

    for region in ('id="status-card"', 'id="event-form"', 'id="timeline"', 'id="reminders"'):
        assert region in page, region
    assert 'hx-target="#status-card"' in page
    assert 'hx-target="#event-form"' in page
    assert "hx-swap-oob" not in page, "out of band is for a fragment, never for the whole page"


def test_every_form_that_swaps_still_has_somewhere_to_post(client, user, application):
    """Progressive by construction, and this is what keeps it so.

    A form that lost its `action` while gaining `hx-post` works until the script does not
    load, which is exactly the day nobody is testing.
    """
    client.force_login(user)

    page = client.get(application.get_absolute_url()).content.decode()

    for url in (
        reverse("applications:status", args=[application.pk]),
        reverse("applications:event_create", args=[application.pk]),
    ):
        assert f'action="{url}"' in page, url
        assert f'hx-post="{url}"' in page, url


def test_one_person_never_swaps_anothers_record(client, other_user, application):
    """The fragment routes are routes, and answer to the same rule as every other."""
    client.force_login(other_user)

    response = client.post(
        reverse("applications:status", args=[application.pk]),
        {"status": Status.OFFER, "note": ""},
        **asking_for("status-card"),
    )

    assert response.status_code == 404
