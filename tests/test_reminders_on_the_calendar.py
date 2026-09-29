"""Reminders inside the calendar: one page where a reminder is seen, made, changed and put
off (#316).

There were two. *Reminders* listed them with their actions; *Calendar* drew them on their
days with none, and said to go to Reminders to make one. What is held here:

- **the actions work from every shape of the calendar with no script at all**: each is a
  plain form or link, and each brings the person back to the shape and the day they
  pressed it on;
- **a reminder is made from the day it is for**, which the form reads from the address and
  checks, ignoring a day it cannot read;
- **the reminders page is the agenda narrowed to reminders**, and shows at least what the
  page did, in its order: everything not yet done, soonest first, however late -- an
  overdue reminder never falls off the front of the agenda -- and however far ahead;
- **the old address still lands**, by a temporary redirect;
- **nothing links to it any more**, and the navigation has one entry where it had two.
"""

from __future__ import annotations

import datetime as dt
import html
import re
from pathlib import Path
from unittest import mock

import pytest
from django.urls import reverse
from django.utils import timezone

from postulo.applications import agenda
from postulo.applications.models import Application, Reminder, Status
from postulo.jobs.models import Company, JobPosting

pytestmark = pytest.mark.django_db

CALENDAR = reverse("applications:calendar")
#: A day far enough off that "today" never lands in the middle of what is being looked at.
DAY = dt.date(2030, 3, 13)


def local(day: dt.date, hour: int = 10) -> dt.datetime:
    return timezone.make_aware(dt.datetime(day.year, day.month, day.day, hour))


def a_reminder(user, summary="Chase them", due_at=None, application=None) -> Reminder:
    return Reminder.objects.create(
        owner=user, application=application, summary=summary, due_at=due_at or local(DAY)
    )


@pytest.fixture
def application(user):
    company = Company.objects.create(owner=user, name="Aperture Science")
    posting = JobPosting.objects.create(owner=user, company=company, title="Test Engineer")
    return Application.objects.create(owner=user, posting=posting, status=Status.APPLIED)


#: Every shape the calendar has, on a period that holds `DAY`.
SHAPES = {
    "month": f"{CALENDAR}?month={DAY:%Y-%m}",
    "week": f"{CALENDAR}?view=week&on={DAY:%Y-%m-%d}",
    "day": f"{CALENDAR}?view=day&on={DAY:%Y-%m-%d}",
    "agenda": f"{CALENDAR}?view=agenda&on={DAY - dt.timedelta(days=3):%Y-%m-%d}",
    "reminders": f"{CALENDAR}?view=agenda&on={DAY:%Y-%m-%d}&kinds=reminder",
}


def link_to(page: str, name: str, reminder: Reminder) -> str:
    """The address of one of a reminder's two links on a page, as a browser would follow it."""
    found = re.search(
        rf'href="({reverse(f"applications:reminder_{name}", args=[reminder.pk])}[^"]*)"', page
    )
    assert found, f"no {name} link for {reminder.summary!r}"
    return html.unescape(found.group(1))


# ------------------------------------------------------------- the actions, every shape


@pytest.mark.parametrize("shape", SHAPES)
def test_every_shape_offers_a_reminders_actions_and_says_where_to_come_back(client, user, shape):
    reminder = a_reminder(user)
    client.force_login(user)
    address = SHAPES[shape]

    page = client.get(address).content.decode()

    assert f'action="{reverse("applications:reminder_complete", args=[reminder.pk])}"' in page
    assert f'action="{reverse("applications:reminder_later", args=[reminder.pk])}"' in page
    assert 'value="tomorrow"' in page and 'value="next_week"' in page
    # Every form carries the page it was drawn on, query and all, and so does every link.
    assert f'name="next" value="{html.escape(address)}"' in page
    assert link_to(page, "update", reminder).endswith(f"?next={_quoted(address)}")
    assert link_to(page, "delete", reminder).endswith(f"?next={_quoted(address)}")


def _quoted(address: str) -> str:
    from urllib.parse import quote

    return quote(address, safe="")


@pytest.mark.parametrize("shape", SHAPES)
def test_done_and_later_are_plain_posts_that_come_back_to_the_same_shape(client, user, shape):
    """With scripts off, which is what the test client is."""
    done = a_reminder(user, "Send the portfolio")
    later = a_reminder(user, "Call Cave", due_at=local(DAY, 15))
    client.force_login(user)
    address = SHAPES[shape]

    response = client.post(
        reverse("applications:reminder_complete", args=[done.pk]), {"next": address}
    )
    assert response.status_code == 302 and response["Location"] == address
    done.refresh_from_db()
    assert done.is_done

    was = later.due_at
    response = client.post(
        reverse("applications:reminder_later", args=[later.pk]),
        {"when": "next_week", "next": address},
    )
    assert response.status_code == 302 and response["Location"] == address
    later.refresh_from_db()
    assert later.due_at != was and later.notified_at is None


@pytest.mark.parametrize("shape", SHAPES)
def test_edit_and_delete_come_back_to_the_same_shape(client, user, shape):
    reminder = a_reminder(user)
    client.force_login(user)
    address = SHAPES[shape]
    page = client.get(address).content.decode()

    edit = link_to(page, "update", reminder)
    form = client.get(edit).content.decode()
    assert f'href="{html.escape(address)}"' in form, "Cancel goes back too"
    response = client.post(
        edit, {"summary": "Chase them properly", "due_at": f"{DAY:%Y-%m-%d}T11:30"}
    )
    assert response.status_code == 302 and response["Location"] == address
    reminder.refresh_from_db()
    assert reminder.summary == "Chase them properly"

    delete = link_to(client.get(address).content.decode(), "delete", reminder)
    confirm = client.get(delete).content.decode()
    assert f'href="{html.escape(address)}"' in confirm, "Cancel goes back too"
    response = client.post(delete)
    assert response.status_code == 302 and response["Location"] == address
    assert not Reminder.objects.filter(pk=reminder.pk).exists()


def test_a_month_cell_keeps_every_action_in_the_menu_done_first(client, user):
    """A cell has no room for a row of buttons beside the reminder's words."""
    reminder = a_reminder(user)
    client.force_login(user)

    page = client.get(SHAPES["month"]).content.decode()

    complete = f'action="{reverse("applications:reminder_complete", args=[reminder.pk])}"'
    form = page[page.index(complete) : page.index("</form>", page.index(complete))]
    assert 'role="menuitem"' in form, "Done is an item of the menu"
    assert page.index(complete) < page.index('value="tomorrow"'), "and the first of them"


def test_a_week_draws_the_row_for_one_column_and_the_menu_for_seven(client, user):
    """Exactly one of the two is ever drawn: the row where a day is as wide as the page, the
    menu from `lg` up, where the days are seven narrow columns."""
    a_reminder(user)
    client.force_login(user)

    page = client.get(SHAPES["week"]).content.decode()

    row = re.search(r'<div class="lg:hidden" data-actions="row">(.*?)</form>', page, re.S)
    column = re.search(r'<div class="hidden lg:block" data-actions="column">', page)
    assert row and column
    assert 'data-size="xs"' in row.group(1), "Done as a button of its own, at hand"


def test_the_menus_open_away_from_the_nearer_edge_of_the_grid():
    today = dt.date(2030, 3, 13)
    weeks = agenda.month_grid(today, [], today=today)
    assert [day.menu_align for day in weeks[0]] == ["start"] * 3 + ["end"] * 4
    week = agenda.week_days(today, [], today=today)
    assert [day.column for day in week] == list(range(7))
    assert week[0].menu_align == "start" and week[-1].menu_align == "end"


def test_a_done_reminder_offers_only_what_can_still_be_done_to_it(client, user):
    reminder = a_reminder(user)
    reminder.complete()
    client.force_login(user)

    for address in SHAPES.values():
        page = client.get(address).content.decode()
        assert reverse("applications:reminder_complete", args=[reminder.pk]) not in page
        assert reverse("applications:reminder_later", args=[reminder.pk]) not in page
        assert reverse("applications:reminder_update", args=[reminder.pk]) in page


def test_a_reminder_about_no_application_opens_its_own_form_and_comes_back(
    client, user, application
):
    mine = a_reminder(user, "Renew the portfolio")
    about = a_reminder(user, "Chase Aperture", application=application)
    client.force_login(user)
    address = SHAPES["day"]

    events = {e.title: e for e in client.get(address).context["page"].events}
    assert events["Chase Aperture"].url == application.get_absolute_url()
    assert events["Renew the portfolio"].url == reverse(
        "applications:reminder_update", args=[mine.pk]
    )
    page = client.get(address).content.decode()
    assert (
        f'href="{reverse("applications:reminder_update", args=[mine.pk])}'
        f'?next={_quoted(address)}"\n     class="cal-event' in page
    )
    assert about.pk


# ---------------------------------------------------------------- making one on a day


def test_the_toolbar_offers_a_new_reminder_and_every_day_its_own(client, user):
    client.force_login(user)

    for shape in ("month", "week", "day", "agenda"):
        address = SHAPES[shape]
        if shape == "agenda":
            a_reminder(user, "So the agenda has a day to draw")
        page = client.get(address).content.decode()
        toolbar = f'href="{reverse("applications:reminder_create")}?next={_quoted(address)}"'
        assert toolbar in page, shape

    month = client.get(SHAPES["month"]).content.decode()
    cell = (
        f'href="{reverse("applications:reminder_create")}?on=2030-03-13&amp;next='
        f'{_quoted(SHAPES["month"])}"'
    )
    assert cell in month
    # Named for its day, not "New reminder" forty times over.
    assert 'aria-label="New reminder on 13 March"' in month
    assert month.count("data-new-reminder") == sum(
        len(week) for week in agenda.month_grid(DAY, [], today=DAY)
    )

    week = client.get(SHAPES["week"]).content.decode()
    assert week.count("data-new-reminder") == 7
    day = client.get(SHAPES["day"]).content.decode()
    assert 'aria-label="New reminder on 13 March"' in day
    assert day.count("data-new-reminder") == 1


def test_the_day_clicked_is_the_forms_day(client, user):
    client.force_login(user)

    form = client.get(reverse("applications:reminder_create"), {"on": "2030-03-13"})

    assert form.status_code == 200
    assert 'value="2030-03-13T09:00"' in form.content.decode()


@pytest.mark.parametrize("raw", ["soon", "2030-02-30", "13/03/2030", "2030-03-13T25:00", ""])
def test_a_day_nobody_can_read_is_ignored(client, user, raw):
    client.force_login(user)

    form = client.get(reverse("applications:reminder_create"), {"on": raw})

    assert form.status_code == 200
    assert not re.search(r'name="due_at"[^>]*value=', form.content.decode())


def test_a_reminder_made_on_a_day_lands_on_it_and_the_person_comes_back(client, user):
    client.force_login(user)
    back = SHAPES["day"]
    address = f"{reverse('applications:reminder_create')}?on=2030-03-13&next={_quoted(back)}"

    response = client.post(address, {"summary": "Post the letter", "due_at": "2030-03-13T09:00"})

    assert response.status_code == 302 and response["Location"] == back
    reminder = Reminder.objects.get(owner=user)
    assert timezone.localtime(reminder.due_at) == local(DAY, 9)
    assert "Post the letter" in client.get(back).content.decode()


def test_without_a_next_a_new_reminder_goes_where_it_always_did(client, user, application):
    client.force_login(user)
    url = reverse("applications:reminder_create")

    about = client.post(
        f"{url}?application={application.pk}",
        {"summary": "Chase", "due_at": "2030-03-13T09:00", "application": application.pk},
    )
    alone = client.post(url, {"summary": "Alone", "due_at": "2030-03-13T09:00"})

    assert about["Location"] == application.get_absolute_url()
    assert alone["Location"] == agenda.reminders_address()


# ----------------------------------------------- the reminders page, as the agenda


@pytest.fixture
def a_search_of_reminders(user):
    """Everything the reminders page could have shown, and two things it would not."""
    now = timezone.now()
    made = {
        "long overdue": a_reminder(user, "Long overdue", now - dt.timedelta(days=40)),
        "overdue": a_reminder(user, "Overdue", now - dt.timedelta(days=3)),
        "soon": a_reminder(user, "Soon", now + dt.timedelta(days=3)),
        "far": a_reminder(user, "Far off", now + dt.timedelta(days=75)),
        "done late": a_reminder(user, "Done late", now - dt.timedelta(days=20)),
        "done soon": a_reminder(user, "Done soon", now + dt.timedelta(days=10)),
    }
    made["done late"].complete()
    made["done soon"].complete()
    return made


def what_the_reminders_page_showed(user) -> list[str]:
    """The reminders page, as it was: everything not yet done, soonest first."""
    return [r.summary for r in Reminder.objects.for_user(user).outstanding().order_by("due_at")]


def outstanding_in_order(page) -> list[str]:
    """What an agenda shows of the reminders not yet done, from its first line to its last."""
    days = [event for _day, events in page.listed for event in events]
    return [
        event.title
        for event in (*page.overdue, *days, *page.further)
        if event.kind == agenda.REMINDER and not event.muted
    ]


def test_the_agenda_narrowed_to_reminders_is_the_reminders_page(
    client, user, a_search_of_reminders
):
    client.force_login(user)

    page = client.get(agenda.reminders_address()).context["page"]

    assert page.view == "agenda" and page.kinds == {agenda.REMINDER}
    assert (
        outstanding_in_order(page)
        == what_the_reminders_page_showed(user)
        == [
            "Long overdue",
            "Overdue",
            "Soon",
            "Far off",
        ]
    )
    assert [e.title for e in page.overdue] == ["Long overdue", "Overdue"]
    assert [e.title for e in page.further] == ["Far off"]
    # Done ones are drawn on their day, struck through, as the calendar draws them; one done
    # before the agenda's first day is not overdue, because it is not outstanding.
    drawn = {e.title: e for _day, events in page.listed for e in events}
    assert drawn["Done soon"].muted
    assert "Done late" not in drawn and "Done late" not in [e.title for e in page.overdue]


@pytest.mark.parametrize("days", [-30, 0, 30, 400])
def test_an_overdue_reminder_never_falls_off_the_front_of_the_agenda(
    client, user, a_search_of_reminders, days
):
    """From whatever day the agenda starts: before its range, it is carried at the front;
    inside it, it is on its day. Never on neither, and never on both."""
    client.force_login(user)
    on = timezone.localdate() + dt.timedelta(days=days)

    page = client.get(
        CALENDAR, {"view": "agenda", "on": on.isoformat(), "kinds": "reminder"}
    ).context["page"]

    shown = outstanding_in_order(page)
    for overdue in ("Long overdue", "Overdue"):
        assert shown.count(overdue) == 1, f"{overdue} from {on}: {shown}"
    assert len(shown) == len(set(shown)), "nothing twice"


def test_an_agenda_from_a_later_day_does_not_call_the_days_before_it_overdue(
    client, user, a_search_of_reminders
):
    """*Soon* is before an agenda starting in a month, and it is not overdue: it is on the
    page before, in its day."""
    client.force_login(user)
    on = timezone.localdate() + dt.timedelta(days=30)

    page = client.get(
        CALENDAR, {"view": "agenda", "on": on.isoformat(), "kinds": "reminder"}
    ).context["page"]

    assert [e.title for e in page.overdue] == ["Long overdue", "Overdue"]
    assert "Soon" not in outstanding_in_order(page)


def test_the_two_ends_are_the_agendas_and_only_where_reminders_are_shown(
    client, user, a_search_of_reminders
):
    client.force_login(user)

    everything = client.get(CALENDAR, {"view": "agenda"}).context["page"]
    assert [e.title for e in everything.overdue] == ["Long overdue", "Overdue"]

    interviews = client.get(CALENDAR, {"view": "agenda", "kinds": "interview"}).context["page"]
    assert not interviews.overdue and not interviews.further

    for view in ("month", "week", "day"):
        page = client.get(CALENDAR, {"view": view}).context["page"]
        assert not page.overdue and not page.further, view


def test_the_overdue_and_the_further_ahead_are_drawn_with_their_day_and_their_actions(
    client, user, a_search_of_reminders
):
    client.force_login(user)
    overdue = a_search_of_reminders["overdue"]
    far = a_search_of_reminders["far"]

    page = client.get(agenda.reminders_address()).content.decode()

    front = page[page.index('data-reminders="overdue"') :]
    front = front[: front.index("</section>")]
    assert "Overdue" in front and "Long overdue" in front
    assert reverse("applications:reminder_complete", args=[overdue.pk]) in front
    from django.utils import formats

    assert formats.date_format(timezone.localtime(overdue.due_at), "DATETIME_FORMAT") in front

    back = page[page.index('data-reminders="further"') :]
    back = back[: back.index("</section>")]
    assert "Further ahead" in back and "Far off" in back
    assert reverse("applications:reminder_later", args=[far.pk]) in back
    assert page.index('data-reminders="overdue"') < page.index('data-reminders="further"')


def test_one_persons_overdue_reminders_are_nobody_elses(client, user, other_user):
    a_reminder(other_user, "Theirs", timezone.now() - dt.timedelta(days=5))
    a_reminder(other_user, "Theirs, later", timezone.now() + dt.timedelta(days=90))
    client.force_login(user)

    page = client.get(agenda.reminders_address()).context["page"]

    assert not page.overdue and not page.further


# ------------------------------------------------------------------- the old address


def test_the_old_address_redirects_for_now_to_the_agenda_of_reminders(client, user):
    client.force_login(user)

    response = client.get("/applications/reminders/")

    assert response.status_code == 302, "temporary: the calendar's shape is young"
    assert response["Location"] == f"{CALENDAR}?view=agenda&kinds=reminder"


def test_the_old_address_carries_across_what_still_means_something(client, user):
    client.force_login(user)
    a_reminder(user, "Overdue", timezone.now() - dt.timedelta(days=2))

    response = client.get("/applications/reminders/", {"show": "all", "on": "2030-03-01"})

    location = response["Location"]
    assert location.startswith(f"{CALENDAR}?")
    assert "show=" not in location, "the calendar always draws what is done"
    assert "on=2030-03-01" in location and "view=agenda" in location
    assert "kinds=reminder" in location

    landed = client.get("/applications/reminders/", follow=True)
    assert landed.status_code == 200 and "Overdue" in landed.content.decode()


def test_a_visitor_who_is_not_signed_in_is_asked_to_sign_in_after_it(client):
    response = client.get("/applications/reminders/", follow=True)
    assert "login" in response.redirect_chain[-1][0]


# --------------------------------------------------------------- nothing links to it


def test_nothing_links_to_the_old_address():
    """Every link to the reminders page goes to the calendar now: the navigation, the
    search, a notification, the forms' way back. The route stays for bookmarks and is
    named nowhere but where it is declared."""
    source = Path(__file__).resolve().parents[1] / "src" / "postulo"
    naming = [
        path.relative_to(source).as_posix()
        for path in source.rglob("*")
        if path.suffix in {".py", ".html", ".js", ".txt"}
        and "reminder_list" in path.read_text(encoding="utf-8", errors="ignore")
    ]
    assert naming == ["applications/urls.py"], naming


def test_the_navigation_has_one_entry_for_both(client, user):
    from postulo.core import navigation

    assert "reminders" not in navigation.BY_KEY
    client.force_login(user)
    home = client.get(reverse("core:home")).content.decode()
    assert 'data-nav="reminders"' not in home and 'data-nav="calendar"' in home

    # The reminder's own pages are the calendar's, so the calendar is the place marked.
    reminder = a_reminder(user)
    for name in ("reminder_create", "reminder_update", "reminder_delete"):
        args = [] if name == "reminder_create" else [reminder.pk]
        page = client.get(reverse(f"applications:{name}", args=args)).content.decode()
        marked = set(re.findall(r'nav-link-active[^>]*data-nav="([^"]+)"', page))
        assert marked == {"calendar"}, (name, marked)


def test_the_calendar_says_where_a_reminder_is_made(client, user):
    client.force_login(user)
    page = client.get(CALENDAR).content.decode()
    assert "a reminder from Reminders" not in page
    assert "make a reminder here, on any day" in page


def test_a_search_finds_a_reminder_on_its_day_and_more_of_them_on_the_calendar(user):
    from postulo.core import search

    due = timezone.now() + dt.timedelta(days=4)
    a_reminder(user, "Renew the portfolio", due)

    group = {g.kind: g for g in search.search(user, "portfolio")}["reminders"]

    assert group.hits[0].url == agenda.url_for("day", timezone.localdate(due))
    assert group.more_url == agenda.reminders_address()


def test_a_notification_about_a_reminder_of_its_own_leads_to_the_calendar(user):
    from postulo.notifications.management.commands.send_due_reminders import (
        announce_due_reminders,
    )
    from postulo.plugins.email import EmailNotifier
    from tests.test_notifications import email_connection

    a_reminder(user, "Renew the portfolio", timezone.now() - dt.timedelta(minutes=1))
    email_connection(user)
    seen = []

    def remember(self, notification, config, recipient):
        seen.append(notification)

    with mock.patch.object(EmailNotifier, "send", remember):
        announce_due_reminders()

    assert seen and seen[0].url.endswith(agenda.reminders_address())
