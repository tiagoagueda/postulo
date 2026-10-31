"""The Calendar printed: a PDF of the month, week, day or agenda on screen, set by a theme (#342).

> a template for printing calendar views

Printing the Calendar was the browser's dialog over the page: two entries a month cell, the
buttons, the legend and the navigation. What is held here: that the Calendar page offers a
print of the view on screen, carrying its question; that each of the four views prints every
entry of its period, in the kinds and the search the address names, whole days included
whatever the instance's address; that what is over is struck and said; that nothing that is a
control prints; that the language, the direction and the paper are the document's, a month on
its side; that a theme sets it -- Postulo's own, or a plugin's that sets the calendar kind --
and is remembered on a POST only; and that a theme is handed plain values.
"""

from __future__ import annotations

import datetime as dt
import sys

import pytest
from django.urls import reverse

from postulo.applications import agenda, calendar_print
from postulo.applications.models import Application, InterviewOutcome, Reminder, Status
from postulo.applications.services import schedule_interview
from postulo.documents import themes
from postulo.jobs.models import Company, JobPosting

pytestmark = pytest.mark.django_db

UTC = dt.UTC
PRINT = "applications:calendar_print"
FILE = "applications:calendar_print_file"


@pytest.fixture(autouse=True)
def _no_plugin_themes(settings):
    # No public address, as on a laptop: whole days print all the same.
    settings.POSTULO_PUBLIC_URL = ""
    yield
    themes.forget()


@pytest.fixture
def drafts(monkeypatch):
    """What the renderer was handed, and a PDF that is not one, so these run without it."""
    seen: list[str] = []

    def draft(html, *args, **kwargs):
        seen.append(html)
        return b"%PDF-1.7 fake"

    monkeypatch.setattr("postulo.documents.pdf.draft_pdf", draft)
    return seen


def at(year, month, day, hour=10, minute=0) -> dt.datetime:
    return dt.datetime(year, month, day, hour, minute, tzinfo=UTC)


@pytest.fixture
def application(user):
    company = Company.objects.create(owner=user, name="Aperture Science")
    posting = JobPosting.objects.create(owner=user, company=company, title="Test Engineer")
    return Application.objects.create(
        owner=user, posting=posting, status=Status.DRAFT, deadline=dt.date(2026, 9, 18)
    )


@pytest.fixture
def september(user, application):
    """An interview, five reminders on one day (one done), and a deadline."""
    schedule_interview(application, kind="video", starts_at=at(2026, 9, 10, 14), remind=False)
    for number in range(5):
        Reminder.objects.create(
            owner=user, summary=f"Chase {number}", due_at=at(2026, 9, 15, 9, number)
        )
    done = Reminder.objects.get(summary="Chase 0")
    done.done_at = at(2026, 9, 15, 12)
    done.save()


def printed(client, drafts, query: str, **form) -> str:
    response = client.post(reverse(FILE) + f"?{query}", {"language": "en-GB", **form})
    assert response.status_code == 200, response.status_code
    assert response["Content-Type"] == "application/pdf"
    assert "attachment" in response["Content-Disposition"]
    return drafts[-1]


# ------------------------------------------------------------- the button and the page


def test_the_calendar_page_offers_a_print_of_the_view_on_screen(client, user):
    client.force_login(user)

    page = client.get(
        reverse("applications:calendar"), {"view": "week", "on": "2026-09-14", "kinds": "reminder"}
    ).content.decode()

    assert "data-calendar-print" in page
    assert "/applications/calendar/print/?view=week&amp;on=2026-09-14&amp;kinds=reminder" in page


def test_the_print_page_says_what_the_print_will_hold(client, user, september):
    client.force_login(user)

    page = client.get(reverse(PRINT), {"month": "2026-09"}).content.decode()

    assert "7 entries." in page
    assert "September 2026" in page
    assert "Everything" in page
    assert '<option value="a4-landscape" selected' in page, "a month, on its side"
    assert f'action="{reverse(FILE)}?month=2026-09"' in page


def test_the_print_page_carries_only_the_calendars_question(client, user):
    client.force_login(user)

    page = client.get(
        reverse(PRINT), {"view": "day", "on": "2026-09-15", "next": "https://evil.example"}
    ).content.decode()

    assert f'action="{reverse(FILE)}?view=day&amp;on=2026-09-15"' in page
    assert f'href="{reverse("applications:calendar")}?view=day&amp;on=2026-09-15"' in page
    assert '<option value="a4-portrait" selected' in page, "a day is a list, upright"


# ----------------------------------------------------------------- what is printed


def test_a_month_prints_every_entry_and_no_control(client, user, september, drafts):
    client.force_login(user)

    html = printed(client, drafts, "month=2026-09")

    for number in range(5):
        assert f"Chase {number}" in html, "every entry, not two a day"
    assert "and 3 more" not in html
    assert "Aperture Science" in html
    assert "Deadline: Test Engineer" in html, "a whole day, though the instance has no address"
    for control in ("New reminder", "Hide Interviews", "Previous", "<a ", "<button", "<form"):
        assert control not in html, control
    assert 'class="month"' in html
    assert "size: A4 landscape" in html


@pytest.mark.parametrize(
    "query,shown,left_out",
    [
        ("view=week&on=2026-09-14", ["Chase 4", "Deadline: Test Engineer"], ["Interview:"]),
        ("view=day&on=2026-09-15", ["Chase 0", "Chase 4"], ["Interview:", "Deadline"]),
        (
            "view=agenda&on=2026-09-01&until=2026-09-30",
            ["Interview:", "Chase 3", "Deadline: Test Engineer"],
            [],
        ),
    ],
)
def test_each_view_prints_its_own_period(client, user, september, drafts, query, shown, left_out):
    client.force_login(user)

    html = printed(client, drafts, query)

    for said in shown:
        assert said in html, said
    for said in left_out:
        assert said not in html, said
    assert 'class="month"' not in html, "the other three are days one under another"


def test_a_week_and_a_day_keep_their_empty_days_and_an_agenda_does_not(
    client, user, september, drafts
):
    client.force_login(user)

    week = printed(client, drafts, "view=week&on=2026-09-14")
    agenda_html = printed(client, drafts, "view=agenda&on=2026-09-01&until=2026-09-30")

    assert week.count("<h2>") == 7
    assert "Nothing on this day." in week
    assert agenda_html.count("<h2>") == 3, "the days with something on"


def test_an_empty_agenda_says_so(client, user, drafts):
    client.force_login(user)

    html = printed(client, drafts, "view=agenda&on=2030-01-01")

    assert "Nothing in this period." in html


def test_the_kinds_and_the_search_of_the_address_are_the_prints(client, user, september, drafts):
    client.force_login(user)

    narrowed = printed(client, drafts, "month=2026-09&kinds=interview")
    searched = printed(client, drafts, "month=2026-09&q=chase%203")

    assert "Aperture Science" in narrowed and "Chase" not in narrowed
    assert "Interviews" in narrowed, "what it is narrowed to, said"
    assert "Chase 3" in searched and "Chase 2" not in searched and "Aperture" not in searched
    assert "1 entry" in searched


def test_what_is_over_is_struck_and_said(client, user, application, drafts):
    interview = schedule_interview(
        application, kind="video", starts_at=at(2026, 9, 10, 14), remind=False
    )
    interview.outcome = InterviewOutcome.CANCELLED
    interview.save()
    Reminder.objects.create(
        owner=user, summary="Chased", due_at=at(2026, 9, 11, 9), done_at=at(2026, 9, 11, 10)
    )
    client.force_login(user)

    html = printed(client, drafts, "view=week&on=2026-09-07")

    assert "Cancelled interview:" in html and "Done reminder:" in html
    assert html.count(' over"') == 2


def test_written_in_the_language_and_the_direction_chosen(client, user, september, drafts):
    client.force_login(user)

    french = printed(client, drafts, "month=2026-09", language="fr-FR")
    arabic = printed(client, drafts, "month=2026-09", language="ar")

    assert 'lang="fr-FR"' in french and "septembre 2026" in french
    assert 'dir="rtl"' in arabic


def test_the_chosen_paper_is_the_one_drawn(client, user, september, drafts):
    client.force_login(user)

    html = printed(client, drafts, "month=2026-09", paper="letter-portrait")

    assert "size: letter portrait" in html


def test_classic_sets_a_calendar_too(client, user, september, drafts):
    client.force_login(user)

    html = printed(client, drafts, "month=2026-09", theme="classic")

    assert "Palatino" in html and "Chase 1" in html


def test_the_file_is_named_for_its_period():
    assert (
        calendar_print.filename(agenda.Asked("month", dt.date(2026, 9, 15), agenda.ALL_KINDS))
        == "postulo-calendar-2026-09.pdf"
    )
    week = agenda.Asked("week", dt.date(2026, 9, 16), agenda.ALL_KINDS)
    assert calendar_print.filename(week).startswith("postulo-calendar-week-2026-09-1")


def test_signed_out_is_sent_to_sign_in(client):
    for name in (PRINT, FILE):
        response = client.get(reverse(name))
        assert response.status_code == 302 and "login" in response["Location"]


def test_nobody_elses_calendar_prints(client, user, other_user, drafts):
    company = Company.objects.create(owner=other_user, name="Their Company")
    posting = JobPosting.objects.create(
        owner=other_user, company=company, title="Their Role", closes_at=dt.date(2026, 9, 20)
    )
    theirs = Application.objects.create(
        owner=other_user, posting=posting, status=Status.DRAFT, deadline=dt.date(2026, 9, 18)
    )
    schedule_interview(theirs, kind="video", starts_at=at(2026, 9, 10, 14), remind=False)
    Reminder.objects.create(owner=other_user, summary="Their reminder", due_at=at(2026, 9, 15))
    client.force_login(user)

    page = client.get(reverse(PRINT), {"month": "2026-09"}).content.decode()
    html = printed(client, drafts, "month=2026-09")

    assert "0 entries." in page
    assert "Their" not in html and "Their" not in page


# ---------------------------------------------------------------------------- themes


def test_postulos_own_themes_set_the_calendar():
    assert themes.Kind.CALENDAR == "calendar"
    assert {theme.name for theme in themes.for_kind(themes.Kind.CALENDAR)} == {"plain", "classic"}


@pytest.fixture
def planner(tmp_path, monkeypatch):
    """A plugin, Planner, whose one theme sets calendars and nothing else, printing what it is
    handed so a test can read it back."""
    from postulo.plugins import themes as plugin_themes

    package = tmp_path / "planner"
    (package / "templates" / "planner").mkdir(parents=True)
    (package / "templates" / "planner" / "calendar.html").write_text(
        '<html lang="{{ document_language }}"><title>{{ document_title }}</title><body>PLANNER'
        "|{{ view }}|{{ title }}|{{ count }}|{{ weeks|length }}|{{ days|length }}"
        "|{% for day in days %}{% for entry in day.entries %}"
        "{{ entry.kind }}={{ entry.title }};{% endfor %}{% endfor %}"
        "</body></html>",
        encoding="utf-8",
    )
    (package / "__init__.py").write_text(
        "from postulo.plugins.api import Theme, ThemeKind\n"
        "class Planner:\n"
        "    name = 'planner'\n"
        "    kind = 'feature'\n"
        "    themes = [Theme(name='planner', label='Planner',\n"
        "                    templates={ThemeKind.CALENDAR: 'planner/calendar.html'},\n"
        "                    provider='Planner Press')]\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    before = list(plugin_themes._registered)

    from planner import Planner

    assert plugin_themes.register_plugin_themes(Planner) == 1
    yield themes.find("planner")
    themes.forget()
    plugin_themes._registered[:] = before
    plugin_themes._forget_the_engines()
    sys.modules.pop("planner", None)


def test_a_plugins_calendar_theme_is_offered_draws_and_is_remembered(
    client, user, september, planner, drafts
):
    client.force_login(user)

    assert "Planner, from Planner Press" in client.get(reverse(PRINT)).content.decode()
    html = printed(client, drafts, "month=2026-09", theme="planner", language="fr-FR")

    assert "PLANNER|month|septembre 2026|7|" in html
    assert "reminder=Chase 4;" in html
    user.profile.refresh_from_db()
    assert user.profile.document_themes == {"calendar": "planner"}
    assert '<option value="planner" selected' in client.get(reverse(PRINT)).content.decode()


def test_a_get_draws_and_remembers_nothing(client, user, september, planner, drafts):
    client.force_login(user)

    client.get(reverse(FILE), {"month": "2026-09", "theme": "planner"})

    assert "PLANNER" in drafts[0]
    user.profile.refresh_from_db()
    assert user.profile.document_themes == {}


def test_a_list_theme_is_not_offered_for_a_calendar(client, user):
    """A theme sets what it has a template for; one that sets lists is not a calendar's."""
    themes.register(
        themes.Theme(name="roster", label="Roster", templates={themes.Kind.LIST: "x.html"})
    )
    client.force_login(user)

    assert 'value="roster"' not in client.get(reverse(PRINT)).content.decode()
    assert calendar_print.theme_for(user, "roster") == "plain"


def test_a_paper_the_theme_does_not_print_on_is_said_on_the_page(client, user, drafts):
    themes.register(
        themes.Theme(
            name="upright",
            label="Upright",
            templates={themes.Kind.CALENDAR: "documents/themes/plain/calendar.html"},
            papers=("a4-portrait",),
        )
    )
    client.force_login(user)

    response = client.post(
        reverse(FILE) + "?month=2026-09", {"theme": "upright", "paper": "a4-landscape"}
    )

    assert response.status_code == 302
    assert response["Location"] == reverse(PRINT) + "?month=2026-09"
    assert not drafts


def test_a_calendar_theme_is_handed_plain_values_only(user, september):
    from django.db.models import Model

    from postulo.core import languages

    asked = agenda.asked_for({"month": "2026-09"})
    with languages.override("en-GB"):
        context = calendar_print.as_context(user, asked, today=dt.date(2026, 9, 15))

    def leaves(value):
        if isinstance(value, dict):
            for inner in value.values():
                yield from leaves(inner)
        elif isinstance(value, list | tuple):
            for inner in value:
                yield from leaves(inner)
        else:
            yield value

    for value in leaves(context):
        assert not isinstance(value, Model)
        assert value is None or isinstance(value, str | int | bool | dt.date), type(value)
    assert set(context) == {
        "view",
        "view_label",
        "title",
        "person",
        "produced_at",
        "start",
        "last",
        "kinds",
        "narrowed",
        "search",
        "weekdays",
        "weeks",
        "days",
        "count",
    }
    assert len(context["weekdays"]) == 7 and all(len(week) == 7 for week in context["weeks"])
    assert sum(len(day["entries"]) for day in context["days"]) == context["count"] == 7
    (today,) = [day for day in context["days"] if day["today"]]
    assert [entry["title"] for entry in today["entries"]] == [f"Chase {n}" for n in range(5)]
    assert today["entries"][0]["over"] and today["entries"][0]["said"] == "Done reminder:"


def test_every_view_hands_the_same_keys(user, september):
    keys = None
    for query in (
        {"month": "2026-09"},
        {"view": "week", "on": "2026-09-14"},
        {"view": "day", "on": "2026-09-15"},
        {"view": "agenda", "on": "2026-09-01"},
    ):
        context = calendar_print.as_context(user, agenda.asked_for(query))
        keys = keys or set(context)
        assert set(context) == keys, query
    day = calendar_print.as_context(user, agenda.asked_for({"view": "day", "on": "2026-09-15"}))
    assert day["weeks"] == [] and len(day["days"]) == 1


# ------------------------------------------------------------------- the plain values


def test_an_item_says_what_it_is_as_the_page_does(user, application):
    Reminder.objects.create(
        owner=user, summary="Done one", due_at=at(2026, 9, 11, 9), done_at=at(2026, 9, 11, 10)
    )
    Reminder.objects.create(owner=user, summary="Open one", due_at=at(2026, 9, 11, 11))

    items = agenda.printed_items(user, dt.date(2026, 9, 1), dt.date(2026, 10, 1))
    events = agenda.events_between(user, dt.date(2026, 9, 1), dt.date(2026, 10, 1))

    assert [item.said for item in items] == [event.said for event in events]
    assert {item.said for item in items} >= {"Done reminder:", "Reminder:", ""}


def test_printed_items_hold_the_whole_days_items_between_withholds(user, application):
    start, end = dt.date(2026, 9, 1), dt.date(2026, 10, 1)

    withheld = agenda.items_between(user, start, end)
    printed_items = agenda.printed_items(user, start, end)

    assert withheld.withheld and not len(withheld)
    assert [item.kind for item in printed_items] == ["deadline"]
    assert agenda.printed_items(user, start, end, kinds={"reminder"}) == ()
