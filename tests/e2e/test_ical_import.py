"""An iCalendar file, in a browser: read, chosen from, and added only when somebody says so (#661).

The unit tests post the form; what they cannot reach is the page as a person meets it, with
its menus built beside the native selects and its rows read by a screen reader. So the one
path that matters is walked once -- upload, choose an application, confirm -- and the review
is measured by axe in both themes with one of every kind of row on it.
"""

from __future__ import annotations

import datetime as dt

import pytest
from playwright.sync_api import Page, expect

from .test_accessibility import (  # noqa: F401
    axe_source,
    describe,
    furnished,
    sign_in,
    violations_on,
)

pytestmark = pytest.mark.e2e


def an_ics(path, *, uid: str = "round-two@example.test"):
    when = (dt.datetime.now(dt.UTC) + dt.timedelta(days=9)).strftime("%Y%m%dT100000Z")
    # Bytes: text mode would turn each CRLF into a CR and a CRLF on Windows.
    path.write_bytes(
        "\r\n".join(
            [
                "BEGIN:VCALENDAR",
                "VERSION:2.0",
                "PRODID:-//Test//EN",
                "BEGIN:VEVENT",
                f"UID:{uid}",
                f"DTSTART:{when}",
                "SUMMARY:Technical round",
                "LOCATION:https://meet.example.test/room",
                "END:VEVENT",
                "BEGIN:VEVENT",
                "UID:floating@example.test",
                "DTSTART:20291015T100000",
                "SUMMARY:No zone given",
                "END:VEVENT",
                "BEGIN:VEVENT",
                "UID:weekly@example.test",
                f"DTSTART:{when}",
                "RRULE:FREQ=WEEKLY",
                "SUMMARY:Stand-up",
                "END:VEVENT",
                "BEGIN:VTODO",
                "UID:task@example.test",
                "SUMMARY:Send the references",
                "DUE;VALUE=DATE:20291020",
                "END:VTODO",
                "END:VCALENDAR",
                "",
            ]
        ).encode()
    )
    return path


def read(page: Page, base: str, path) -> None:
    page.goto(f"{base}/applications/calendar/import/")
    page.locator("input[type=file]").set_input_files(str(path))
    page.locator("[data-ical-upload] button[type=submit]").click()
    expect(page.locator("[data-ical-review]")).to_be_visible()


def test_an_event_becomes_an_interview_when_somebody_says_so(
    live_server,
    page: Page,
    furnished,  # noqa: F811
    tmp_path,
):
    from postulo.applications.models import Application, EventKind, Interview

    base = live_server.url
    me = furnished["applicant"]
    application = Application.objects.for_user(me).get(posting__title="Test Engineer")
    sign_in(page, base)

    read(page, base, an_ics(tmp_path / "meetings.ics"))

    assert not Interview.objects.filter(uid="round-two@example.test").exists(), "reading adds none"
    expect(page.locator("[data-outcome='cannot']")).to_contain_text("It repeats")
    expect(page.locator("[data-needs-zone]")).to_contain_text("without a time zone")

    first = page.locator("[data-ical-rows] > li").first
    first.locator("select[name$='-action']").select_option("interview")
    first.locator("select[name$='-application']").select_option(str(application.pk))
    page.get_by_role("button", name="Add what I chose").click()

    expect(page).to_have_url(f"{base}/applications/calendar/")
    expect(page.get_by_text("One entry was added")).to_be_visible()
    interview = Interview.objects.get(uid="round-two@example.test")
    assert interview.application == application
    assert interview.location == "https://meet.example.test/room"
    assert application.events.filter(
        kind=EventKind.INTERVIEW_SCHEDULED, actor__contains="meetings.ics"
    ).exists(), "the timeline says where it came from"

    read(page, base, tmp_path / "meetings.ics")
    expect(page.locator("[data-outcome='present']")).to_have_count(1)


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_review_has_no_violations(
    live_server,
    page: Page,
    axe_source,  # noqa: F811
    furnished,  # noqa: F811
    tmp_path,
    scheme,
):
    from postulo.applications.models import Application, Interview
    from postulo.applications.services import schedule_interview

    base = live_server.url
    application = Application.objects.for_user(furnished["applicant"]).get(
        posting__title="Test Engineer"
    )
    Interview.objects.filter(owner=furnished["applicant"]).delete()
    schedule_interview(
        application,
        kind="video",
        starts_at=dt.datetime.now(dt.UTC) + dt.timedelta(days=30),
        uid="round-two@example.test",
    )
    page.emulate_media(color_scheme=scheme)
    sign_in(page, base)

    read(page, base, an_ics(tmp_path / "meetings.ics"))

    for outcome in ("add", "cannot", "present"):
        expect(page.locator(f"[data-outcome='{outcome}']").first).to_be_visible()
    found = violations_on(page, axe_source)
    assert not found, describe(f"/applications/calendar/import/ review ({scheme})", found)
