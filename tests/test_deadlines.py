"""Deadlines and closing dates, and reminders that can be moved or removed (#238).

Two things Postulo held and did not act on, found in the 2026-09-15 audit.

`Application.deadline` and `JobPosting.closes_at` were columns from the first migration.
Neither was ever drawn on the calendar, neither went into the feed, and the closing date was
counted on the dashboard and nowhere else — so the two dates that actually close were the two
a person had to remember by themselves.

A reminder could be made and it could be ticked off. There was no edit and no delete, in the
interface or in the API, and the only postponement was *Snooze* on a quiet application, which
makes a *new* reminder each time it is pressed. A due time typed wrongly could only be ticked
off as though it had been dealt with, which is the record being falsified to work around a
missing button.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.urls import reverse
from django.utils import timezone

from postulo.applications import agenda
from postulo.applications.models import Application, Reminder, Status
from postulo.applications.services import later_time, postpone_reminder
from postulo.jobs import closing
from postulo.jobs.models import Company, JobPosting, ListingState

pytestmark = pytest.mark.django_db

CALENDAR = "applications:calendar"


@pytest.fixture
def company(user):
    return Company.objects.create(owner=user, name="Aperture Science")


@pytest.fixture
def posting(user, company):
    return JobPosting.objects.create(owner=user, company=company, title="Test Engineer")


@pytest.fixture
def application(user, posting):
    return Application.objects.create(owner=user, posting=posting, status=Status.DRAFT)


def a_reminder(user, application=None, **fields):
    return Reminder.objects.create(
        owner=user,
        application=application,
        summary=fields.pop("summary", "Chase them"),
        due_at=fields.pop("due_at", timezone.now() + dt.timedelta(days=1)),
        **fields,
    )


# ------------------------------------------------------------ on the calendar


def test_a_deadline_and_a_closing_date_are_on_the_calendar(user, application, posting):
    today = timezone.localdate()
    application.deadline = today + dt.timedelta(days=3)
    application.save(update_fields=["deadline"])
    posting.closes_at = today + dt.timedelta(days=4)
    posting.save(update_fields=["closes_at"])

    events = agenda.events_between(user, today, today + dt.timedelta(days=10))

    kinds = {event.kind for event in events}
    assert kinds == {agenda.DEADLINE, agenda.CLOSING}
    deadline = next(e for e in events if e.kind == agenda.DEADLINE)
    assert deadline.all_day, "nobody knows the hour an employer stops reading"
    assert "Test Engineer" in deadline.title
    assert deadline.detail == "Aperture Science"
    assert deadline.url == application.get_absolute_url()


def test_a_whole_day_lands_on_its_own_day_in_the_persons_zone(user, application):
    """`Event.day` is what the grid groups by, and a date has to survive becoming one."""
    today = timezone.localdate()
    application.deadline = today + dt.timedelta(days=2)
    application.save(update_fields=["deadline"])

    (event,) = agenda.events_between(user, today, today + dt.timedelta(days=10))

    assert event.day == application.deadline


def test_a_deadline_already_met_is_drawn_as_over_rather_than_dropped(user, application):
    """The calendar is a record of the month as well as a plan for it, which is the rule a
    done reminder and a cancelled interview already follow."""
    today = timezone.localdate()
    application.deadline = today + dt.timedelta(days=2)
    application.save(update_fields=["deadline"])

    (before,) = agenda.events_between(user, today, today + dt.timedelta(days=10))
    assert not before.muted

    application.applied_at = timezone.now()
    application.save(update_fields=["applied_at"])

    (after,) = agenda.events_between(user, today, today + dt.timedelta(days=10))
    assert after.muted


def test_a_listing_already_applied_to_is_drawn_as_over(user, posting, application):
    today = timezone.localdate()
    posting.closes_at = today + dt.timedelta(days=2)
    posting.save(update_fields=["closes_at"])

    (event,) = agenda.events_between(
        user, today, today + dt.timedelta(days=10), kinds=[agenda.CLOSING]
    )

    assert event.muted, "an application exists for it"


def test_nobody_sees_another_persons_dates(user, other_user, application, posting):
    today = timezone.localdate()
    application.deadline = today + dt.timedelta(days=1)
    application.save(update_fields=["deadline"])
    posting.closes_at = today + dt.timedelta(days=1)
    posting.save(update_fields=["closes_at"])

    assert agenda.events_between(other_user, today, today + dt.timedelta(days=10)) == []


# ------------------------------------------------------------ the key and the filter


def test_the_kinds_can_be_narrowed_and_nonsense_never_empties_the_calendar():
    assert agenda.kinds_from("deadline") == {agenda.DEADLINE}
    assert agenda.kinds_from("deadline,closing") == {agenda.DEADLINE, agenda.CLOSING}
    assert agenda.kinds_from("deadline, nonsense") == {agenda.DEADLINE}
    assert agenda.kinds_from("nonsense") == agenda.ALL_KINDS, "a mistyped address is not empty"
    assert agenda.kinds_from("") == agenda.ALL_KINDS


def test_narrowing_leaves_the_other_kinds_unread(user, application, posting):
    today = timezone.localdate()
    application.deadline = today + dt.timedelta(days=1)
    application.save(update_fields=["deadline"])
    posting.closes_at = today + dt.timedelta(days=1)
    posting.save(update_fields=["closes_at"])
    a_reminder(user, application, due_at=timezone.now() + dt.timedelta(hours=2))

    only = agenda.events_between(
        user, today, today + dt.timedelta(days=10), kinds=[agenda.DEADLINE]
    )

    assert [event.kind for event in only] == [agenda.DEADLINE]


def test_the_kinds_travel_with_every_link_on_the_page(user):
    page = agenda.build(user, "month", timezone.localdate(), kinds=[agenda.DEADLINE])

    assert "kinds=deadline" in page.earlier
    assert "kinds=deadline" in page.later
    assert "kinds=deadline" in page.today
    assert all("kinds=deadline" in url for _view, _label, url, _current in page.switcher)
    assert page.narrowed
    assert "kinds=" not in page.everything, "the way back is the address without one"


def test_showing_everything_puts_no_parameter_on_anything(user):
    page = agenda.build(user, "month", timezone.localdate())

    assert "kinds=" not in page.earlier
    assert not page.narrowed


def test_the_legend_is_the_switch(user):
    page = agenda.build(user, "month", timezone.localdate(), kinds=[agenda.DEADLINE])
    rows = {kind: (showing, url) for kind, _label, _tone, showing, url in page.legend}

    assert rows[agenda.DEADLINE][0] is True
    assert rows[agenda.INTERVIEW][0] is False
    # The one that is showing offers to take itself out -- but taking out the last one
    # would leave a calendar of nothing, so it gives everything back instead.
    assert "kinds=" not in rows[agenda.DEADLINE][1]
    assert (
        "kinds=deadline,interview"
        in rows[agenda.INTERVIEW][1].replace("kinds=interview,deadline", "kinds=deadline,interview")
        or "interview" in rows[agenda.INTERVIEW][1]
    )


def test_the_page_draws_the_legend_and_narrows_from_the_address(client, user, application):
    today = timezone.localdate()
    application.deadline = today
    application.save(update_fields=["deadline"])
    a_reminder(user, application, due_at=timezone.now())
    client.force_login(user)

    whole = client.get(reverse(CALENDAR)).content.decode()
    assert 'data-event="deadline"' in whole and 'data-event="reminder"' in whole
    assert "data-legend" in whole

    narrowed = client.get(reverse(CALENDAR), {"kinds": "deadline"}).content.decode()
    assert 'data-event="deadline"' in narrowed
    assert 'data-event="reminder"' not in narrowed


def test_every_kind_the_legend_names_is_painted():
    """A kind with no rule behind it draws an unstyled box, which reads as a broken
    stylesheet rather than a missing line in one."""
    from pathlib import Path

    css = (Path(__file__).resolve().parents[1] / "assets" / "css" / "app.css").read_text("utf-8")
    for _kind, _label, tone in agenda.KINDS:
        assert f".cal-{tone} {{" in css


def test_what_a_kind_is_called_is_said_in_words_as_well_as_colour():
    """#274: colour is a second signal and never the only one. For an interview and a
    reminder the word is `sr-only`; for the other two the visible title carries it."""
    for kind, _label, _tone in agenda.KINDS:
        said = agenda.SPOKEN[(kind, False)]
        assert said or kind in (agenda.DEADLINE, agenda.CLOSING, agenda.ANSWER)


# ------------------------------------------------------------------- the feed


def test_the_feed_carries_the_dates_as_whole_days(client, user, application, posting):
    today = timezone.localdate()
    application.deadline = today + dt.timedelta(days=5)
    application.save(update_fields=["deadline"])
    posting.closes_at = today + dt.timedelta(days=6)
    posting.save(update_fields=["closes_at"])
    client.force_login(user)

    text = client.get(reverse("applications:interview_calendar")).content.decode()

    assert f"DTSTART;VALUE=DATE:{application.deadline:%Y%m%d}" in text
    # RFC 5545's end is exclusive: a one-day entry ending on its own date is zero days long.
    assert f"DTEND;VALUE=DATE:{application.deadline + dt.timedelta(days=1):%Y%m%d}" in text
    assert "TRANSP:TRANSPARENT" in text, "a deadline does not make somebody look busy all day"
    assert text.count("BEGIN:VEVENT") == 2


def test_one_interviews_file_carries_only_that_interview(client, user, application):
    from postulo.applications.services import schedule_interview

    application.deadline = timezone.localdate() + dt.timedelta(days=5)
    application.save(update_fields=["deadline"])
    interview = schedule_interview(
        application, kind="video", starts_at=timezone.now() + dt.timedelta(days=1)
    )
    client.force_login(user)

    text = client.get(reverse("applications:interview_ics", args=[interview.pk])).content.decode()

    assert text.count("BEGIN:VEVENT") == 1
    assert "VALUE=DATE" not in text


def test_a_day_entry_keeps_its_name_between_fetches(client, user, application):
    application.deadline = timezone.localdate() + dt.timedelta(days=5)
    application.save(update_fields=["deadline"])
    client.force_login(user)

    first = client.get(reverse("applications:interview_calendar")).content.decode()
    second = client.get(reverse("applications:interview_calendar")).content.decode()

    uid = next(line for line in first.splitlines() if line.startswith("UID:"))
    assert uid in second, "a changing UID doubles the entry in somebody's calendar"


# ------------------------------------------------------- telling somebody about it


def test_only_undecided_listings_are_counted_as_closing(user, company, posting, application):
    """A listing applied to is not urgent, a discarded one is not wanted, a closed one has
    closed. The same predicate the dashboard and the table already share."""
    today = timezone.localdate()
    posting.closes_at = today + dt.timedelta(days=1)
    posting.save(update_fields=["closes_at"])
    assert list(closing.closing_for(user)) == [], "this one has an application"

    open_one = JobPosting.objects.create(
        owner=user, company=company, title="Portal Researcher", closes_at=today + dt.timedelta(1)
    )
    assert list(closing.closing_for(user)) == [open_one]

    open_one.state = ListingState.DISCARDED
    open_one.save(update_fields=["state"])
    assert list(closing.closing_for(user)) == []


def test_a_listing_closing_beyond_the_notice_is_not_yet_announced(user, company):
    today = timezone.localdate()
    JobPosting.objects.create(
        owner=user, company=company, title="Far off", closes_at=today + dt.timedelta(days=30)
    )

    assert closing.announce_closing_postings() == (0, 0)


def test_a_listing_about_to_close_is_announced_once(user, company):
    today = timezone.localdate()
    posting = JobPosting.objects.create(
        owner=user, company=company, title="Portal Researcher", closes_at=today + dt.timedelta(1)
    )

    stamped, _delivered = closing.announce_closing_postings()
    assert stamped == 1
    posting.refresh_from_db()
    assert posting.closing_announced_for == posting.closes_at

    assert closing.announce_closing_postings() == (0, 0), "and not again"


def test_moving_the_closing_date_is_news(user, company):
    """The stamp holds the date it was written *for*, so a deadline brought forward is
    announced rather than swallowed."""
    today = timezone.localdate()
    posting = JobPosting.objects.create(
        owner=user, company=company, title="Portal Researcher", closes_at=today + dt.timedelta(3)
    )
    closing.announce_closing_postings()

    posting.closes_at = today + dt.timedelta(days=1)
    posting.save(update_fields=["closes_at"])

    assert closing.announce_closing_postings()[0] == 1


def test_the_notice_follows_the_person(user, company):
    from postulo.accounts.models import Profile

    today = timezone.localdate()
    JobPosting.objects.create(
        owner=user, company=company, title="Portal Researcher", closes_at=today + dt.timedelta(10)
    )
    assert closing.announce_closing_postings() == (0, 0)

    Profile.objects.filter(user=user).update(closing_notice_days=14)
    user.refresh_from_db()

    assert closing.announce_closing_postings()[0] == 1


def test_the_event_is_one_a_notifier_can_be_switched_off_for():
    from postulo.notifications.base import EVENTS

    assert "posting_closing" in EVENTS


def test_the_announcement_names_the_listings(user, company):
    today = timezone.localdate()
    JobPosting.objects.create(
        owner=user, company=company, title="Portal Researcher", closes_at=today + dt.timedelta(1)
    )
    rows = list(closing.closing_for(user))

    message = closing._announcement(rows, today)

    assert message.event == "posting_closing"
    assert "Portal Researcher" in message.body
    assert message.key == f"posting_closing:{rows[0].pk}"
    assert message.data["posting_ids"] == [rows[0].pk]


# ----------------------------------------------------------------- reminders


def test_later_moves_the_reminder_and_lets_it_be_announced_again(user):
    reminder = a_reminder(user, due_at=timezone.now() - dt.timedelta(hours=1))
    Reminder.objects.filter(pk=reminder.pk).update(notified_at=timezone.now())
    reminder.refresh_from_db()

    postpone_reminder(reminder, timezone.now() + dt.timedelta(days=1))

    reminder.refresh_from_db()
    assert reminder.notified_at is None, "moved into the future is not yet announced"
    assert reminder.due_at > timezone.now()


def test_a_reminder_already_done_is_left_where_it_is(user):
    """Putting off something finished is a mistake or a request to reopen it, and reopening
    is not what a button called *Later* should quietly do."""
    reminder = a_reminder(user)
    reminder.complete()
    was = reminder.due_at

    postpone_reminder(reminder, timezone.now() + dt.timedelta(days=7))

    reminder.refresh_from_db()
    assert reminder.due_at == was


def test_tomorrow_and_next_week_keep_the_hour_they_were_set_for(user):
    now = timezone.now()

    assert later_time("tomorrow", now=now) == now + dt.timedelta(days=1)
    assert later_time("next_week", now=now) == now + dt.timedelta(days=7)
    assert later_time("whenever") is None


def test_the_row_offers_later_edit_and_delete(client, user, application):
    reminder = a_reminder(user, application)
    client.force_login(user)

    html = client.get(reverse("applications:reminder_list")).content.decode()

    assert reverse("applications:reminder_later", args=[reminder.pk]) in html
    assert reverse("applications:reminder_update", args=[reminder.pk]) in html
    assert reverse("applications:reminder_delete", args=[reminder.pk]) in html
    assert 'value="tomorrow"' in html and 'value="next_week"' in html


def test_an_applications_page_offers_the_same(client, user, application):
    reminder = a_reminder(user, application)
    client.force_login(user)

    html = client.get(application.get_absolute_url()).content.decode()

    assert reverse("applications:reminder_later", args=[reminder.pk]) in html
    assert reverse("applications:reminder_delete", args=[reminder.pk]) in html


def test_putting_one_off_from_the_page(client, user):
    reminder = a_reminder(user, due_at=timezone.now())
    client.force_login(user)

    client.post(reverse("applications:reminder_later", args=[reminder.pk]), {"when": "next_week"})

    reminder.refresh_from_db()
    assert reminder.due_at > timezone.now() + dt.timedelta(days=6)


def test_a_chosen_day_is_read_in_the_persons_own_zone(client, user):
    reminder = a_reminder(user)
    client.force_login(user)
    wanted = (timezone.localdate() + dt.timedelta(days=10)).isoformat()

    client.post(reverse("applications:reminder_later", args=[reminder.pk]), {"due_at": wanted})

    reminder.refresh_from_db()
    assert timezone.localdate(reminder.due_at).isoformat() == wanted


def test_a_time_nobody_can_read_moves_nothing(client, user):
    reminder = a_reminder(user)
    was = reminder.due_at
    client.force_login(user)

    client.post(reverse("applications:reminder_later", args=[reminder.pk]), {"due_at": "whenever"})

    reminder.refresh_from_db()
    assert reminder.due_at == was


def test_editing_a_reminder_moves_it_and_unstamps_it(client, user, application):
    reminder = a_reminder(user, application, due_at=timezone.now() - dt.timedelta(hours=1))
    Reminder.objects.filter(pk=reminder.pk).update(notified_at=timezone.now())
    client.force_login(user)
    when = timezone.localtime(timezone.now() + dt.timedelta(days=3)).strftime("%Y-%m-%dT%H:%M")

    response = client.post(
        reverse("applications:reminder_update", args=[reminder.pk]),
        {"summary": "Chase them again", "due_at": when, "application": application.pk},
    )

    assert response.status_code == 302
    reminder.refresh_from_db()
    assert reminder.summary == "Chase them again"
    assert reminder.notified_at is None


def test_deleting_a_reminder(client, user, application):
    reminder = a_reminder(user, application)
    client.force_login(user)

    response = client.post(reverse("applications:reminder_delete", args=[reminder.pk]))

    assert response.status_code == 302
    assert not Reminder.objects.filter(pk=reminder.pk).exists()


# ----------------------------------------------------------------- the API


def token_for(user, scopes="read write"):
    from postulo.api.models import ApiToken

    _token, raw = ApiToken.issue(owner=user, name="test", scopes=scopes.split())
    return {"HTTP_AUTHORIZATION": f"Bearer {raw}"}


def test_the_api_can_change_a_reminder(client, user, application):
    reminder = a_reminder(user, application, due_at=timezone.now() - dt.timedelta(hours=1))
    Reminder.objects.filter(pk=reminder.pk).update(notified_at=timezone.now())
    headers = token_for(user)
    when = (timezone.now() + dt.timedelta(days=2)).isoformat()

    response = client.patch(
        f"/api/v1/reminders/{reminder.pk}",
        data={"summary": "Ring them", "due_at": when},
        content_type="application/json",
        **headers,
    )

    assert response.status_code == 200, response.content
    reminder.refresh_from_db()
    assert reminder.summary == "Ring them"
    assert reminder.notified_at is None, "moved into the future is not yet announced"


def test_the_api_leaves_out_what_it_was_not_sent(client, user, application):
    reminder = a_reminder(user, application, summary="Chase them")
    headers = token_for(user)

    client.patch(
        f"/api/v1/reminders/{reminder.pk}",
        data={"summary": "Ring them"},
        content_type="application/json",
        **headers,
    )

    reminder.refresh_from_db()
    assert reminder.application_id == application.pk, "not sent is not the same as cleared"


def test_the_api_can_take_a_reminder_off_an_application(client, user, application):
    reminder = a_reminder(user, application)
    headers = token_for(user)

    client.patch(
        f"/api/v1/reminders/{reminder.pk}",
        data={"application_id": None},
        content_type="application/json",
        **headers,
    )

    reminder.refresh_from_db()
    assert reminder.application_id is None


def test_the_api_can_delete_a_reminder(client, user):
    reminder = a_reminder(user)
    headers = token_for(user)

    response = client.delete(f"/api/v1/reminders/{reminder.pk}", **headers)

    assert response.status_code == 204
    assert not Reminder.objects.filter(pk=reminder.pk).exists()


def test_changing_and_deleting_need_the_write_scope(client, user):
    reminder = a_reminder(user)
    headers = token_for(user, "read")

    changed = client.patch(
        f"/api/v1/reminders/{reminder.pk}",
        data={"summary": "x"},
        content_type="application/json",
        **headers,
    )
    removed = client.delete(f"/api/v1/reminders/{reminder.pk}", **headers)

    assert changed.status_code == 403
    assert removed.status_code == 403
    assert Reminder.objects.filter(pk=reminder.pk).exists()
