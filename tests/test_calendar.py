"""The calendar: a month of interviews and reminders, drawn by the server (#204).

What is held: the grid is whole weeks in the locale's week, the events are read in the
person's own days, a reminder that is done is drawn as done rather than dropped, a cell
holds two and counts the rest, every shape is an address, and one person never sees
another's.
"""

from __future__ import annotations

import datetime as dt
import zoneinfo

import pytest
from django.urls import reverse
from django.utils import timezone, translation

from postulo.accounts.models import Profile
from postulo.applications import agenda
from postulo.applications.models import Application, InterviewOutcome, Reminder, Status
from postulo.applications.services import schedule_interview
from postulo.core import navigation
from postulo.jobs.models import Company, JobPosting

pytestmark = pytest.mark.django_db

CALENDAR = "applications:calendar"
UTC = dt.UTC


@pytest.fixture
def application(user):
    company = Company.objects.create(owner=user, name="Aperture Science")
    posting = JobPosting.objects.create(owner=user, company=company, title="Test Engineer")
    return Application.objects.create(owner=user, posting=posting, status=Status.APPLIED)


def at(year, month, day, hour=10, minute=0, tz=UTC) -> dt.datetime:
    return dt.datetime(year, month, day, hour, minute, tzinfo=tz)


# ---------------------------------------------------------------------- the grid


def test_the_month_is_whole_weeks_starting_on_the_locales_first_day():
    with translation.override("en"):
        weeks = agenda.month_grid(dt.date(2026, 9, 15), [], today=dt.date(2026, 9, 15))
        expected_first = agenda.week_start(dt.date(2026, 9, 1))
    assert all(len(week) == 7 for week in weeks)
    first = weeks[0][0].date
    assert first == expected_first, "the locale's week, not ISO's"
    assert first <= dt.date(2026, 9, 1) < first + dt.timedelta(days=7)
    last = weeks[-1][-1].date
    assert last - dt.timedelta(days=6) <= dt.date(2026, 9, 30) <= last
    assert all(day.outside == (day.date.month != 9) for week in weeks for day in week)
    assert sum(day.today for week in weeks for day in week) == 1


def test_the_months_either_side_are_the_earlier_and_later_links(client, user):
    client.force_login(user)
    page = client.get(reverse(CALENDAR), {"month": "2026-01"}).context["page"]
    assert page.earlier.endswith("?month=2025-12") and page.later.endswith("?month=2026-02")
    assert "January 2026" == str(page.title)


def test_an_unreadable_month_is_this_month(client, user):
    client.force_login(user)
    today = timezone.localdate()
    page = client.get(reverse(CALENDAR), {"month": "next-tuesday"}).context["page"]
    assert page.on == today and page.view == "month"


# -------------------------------------------------------------------- the events


def test_interviews_and_reminders_are_the_events_of_their_days(user, application):
    schedule_interview(application, kind="video", starts_at=at(2026, 9, 10, 14), remind=False)
    Reminder.objects.create(
        owner=user, application=application, summary="Chase", due_at=at(2026, 9, 12, 9)
    )
    Reminder.objects.create(owner=user, summary="Outside", due_at=at(2026, 10, 1, 9))

    events = agenda.events_between(user, dt.date(2026, 9, 1), dt.date(2026, 10, 1))

    assert [(e.kind, e.day) for e in events] == [
        ("interview", dt.date(2026, 9, 10)),
        ("reminder", dt.date(2026, 9, 12)),
    ]
    interview, reminder = events
    assert interview.title == "Aperture Science" and interview.is_span
    assert "Video call" in interview.detail and "Test Engineer" in interview.detail
    assert interview.url == application.interviews.get().get_absolute_url()
    assert reminder.title == "Chase" and reminder.url == application.get_absolute_url()


def test_the_days_are_the_persons_own(user, application):
    """Half past eleven at night in Lisbon is the day it was set for, not the UTC day after."""
    profile, _made = Profile.objects.get_or_create(user=user)
    profile.time_zone = "Pacific/Auckland"
    profile.save()
    Reminder.objects.create(owner=user, summary="Late", due_at=at(2026, 9, 14, 23, 30))

    with timezone.override(zoneinfo.ZoneInfo("Pacific/Auckland")):
        events = agenda.events_between(user, dt.date(2026, 9, 15), dt.date(2026, 9, 16))
        assert [e.title for e in events] == ["Late"]
        assert events[0].day == dt.date(2026, 9, 15)
        assert not agenda.events_between(user, dt.date(2026, 9, 14), dt.date(2026, 9, 15))


def test_what_is_over_is_drawn_as_over_rather_than_dropped(user, application):
    done = Reminder.objects.create(owner=user, summary="Done", due_at=at(2026, 9, 3))
    done.complete()
    interview = schedule_interview(
        application, kind="onsite", starts_at=at(2026, 9, 4), remind=False
    )
    interview.outcome = InterviewOutcome.CANCELLED
    interview.save()

    events = agenda.events_between(user, dt.date(2026, 9, 1), dt.date(2026, 10, 1))

    assert [(e.kind, e.muted) for e in events] == [("reminder", True), ("interview", True)]


def test_what_is_over_says_so_to_a_screen_reader_and_is_not_faded(client, user):
    """The strike says "over" to eyes; the word says it to a screen reader, and the fading
    that went with the strike took 12-pixel text under 4.5:1 (#274)."""
    done = Reminder.objects.create(owner=user, summary="Done", due_at=at(2026, 9, 3))
    done.complete()
    client.force_login(user)

    html = client.get(reverse(CALENDAR), {"month": "2026-09"}).content.decode()

    assert "Done reminder:" in html
    assert "line-through" in html and "opacity-60" not in html


def test_one_person_never_sees_another(user, other_user):
    Reminder.objects.create(owner=other_user, summary="Theirs", due_at=at(2026, 9, 3))
    assert agenda.events_between(user, dt.date(2026, 9, 1), dt.date(2026, 10, 1)) == []


# ----------------------------------------------------------------------- the page


def test_the_page_opens_on_this_month_and_needs_an_account(client, user):
    response = client.get(reverse(CALENDAR))
    assert response.status_code == 302 and "login" in response["Location"]

    client.force_login(user)
    response = client.get(reverse(CALENDAR))
    assert response.status_code == 200
    assert response.context["page"].view == "month"
    assert response.context["page"].on == timezone.localdate()
    assert 'data-calendar="month"' in response.content.decode()


def test_a_cell_holds_two_and_counts_the_rest_and_the_day_opens_on_them(client, user, application):
    for hour in (9, 11, 13, 15):
        Reminder.objects.create(owner=user, summary=f"At {hour}", due_at=at(2026, 9, 8, hour))
    client.force_login(user)

    html = client.get(reverse(CALENDAR), {"month": "2026-09"}).content.decode()
    cell = html.split("8 September")[1].split("</td>")[0]
    assert cell.count("data-event=") == 2
    assert "and 2 more" in cell
    day_url = reverse(CALENDAR) + "?view=day&amp;on=2026-09-08"
    assert day_url in cell

    day = client.get(reverse(CALENDAR), {"view": "day", "on": "2026-09-08"}).content.decode()
    assert day.count("data-event=") == 4
    # The heading is the weekday and then DATE_FORMAT, which British English writes with
    # the month abbreviated; it used to spell the month out and to do so in every
    # language at once (#225).
    assert "Tuesday 8 Sep 2026" in day


def test_week_day_and_agenda_are_the_same_events_in_another_shape(client, user, application):
    schedule_interview(application, kind="phone", starts_at=at(2026, 9, 10, 14), remind=False)
    Reminder.objects.create(owner=user, summary="Chase", due_at=at(2026, 9, 25, 9))
    client.force_login(user)

    week = client.get(reverse(CALENDAR), {"view": "week", "on": "2026-09-10"})
    assert week.context["page"].start == agenda.week_start(dt.date(2026, 9, 10))
    assert 'data-calendar="week"' in week.content.decode()
    assert week.content.decode().count("data-event=") == 1

    day = client.get(reverse(CALENDAR), {"view": "day", "on": "2026-09-25"})
    assert day.content.decode().count("data-event=") == 1 and "Chase" in day.content.decode()

    both = client.get(reverse(CALENDAR), {"view": "agenda", "on": "2026-09-01"})
    page = both.context["page"]
    assert page.end == dt.date(2026, 10, 1)
    assert [d for d, _events in page.listed] == [dt.date(2026, 9, 10), dt.date(2026, 9, 25)]
    assert both.content.decode().count("data-event=") == 2

    nothing = client.get(reverse(CALENDAR), {"view": "agenda", "on": "2030-01-01"}).content.decode()
    assert "Nothing in this period" in nothing

    sideways = client.get(reverse(CALENDAR), {"view": "sideways", "on": "2026-09-10"})
    assert sideways.context["page"].view == "month"


def test_every_shape_is_an_address_the_switcher_offers(client, user):
    client.force_login(user)
    page = client.get(reverse(CALENDAR), {"view": "week", "on": "2026-09-10"}).context["page"]
    urls = {view: url for view, _label, url, _current in page.switcher}
    assert urls["month"].endswith("?month=2026-09")
    assert urls["day"].endswith("?view=day&on=2026-09-10")
    assert urls["agenda"].endswith("?view=agenda&on=2026-09-10")
    assert [current for _v, _l, _u, current in page.switcher] == [False, True, False, False]


# ------------------------------------------------------------------ the navigation


def test_calendar_sits_beside_reminders_in_the_navigation_and_can_be_hidden(client, user):
    keys = [item.key for item in navigation.ITEMS]
    assert keys.index("calendar") == keys.index("reminders") + 1
    assert "calendar" in navigation.HIDEABLE

    client.force_login(user)
    html = client.get(reverse("core:home")).content.decode()
    assert reverse(CALENDAR) in html
    client.post(
        reverse("settings:appearance"),
        {
            "theme": "system",
            "density": "comfortable",
            "navigation": [k for k in navigation.HIDEABLE if k != "calendar"],
        },
    )
    html = client.get(reverse("core:home")).content.decode()
    assert 'data-nav="calendar"' not in html
