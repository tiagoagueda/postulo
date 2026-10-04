"""Putting a reminder off, and narrowing the calendar, in a real browser (#238), and the
reminders living on the calendar (#316).

`tests/test_deadlines.py` and `tests/test_reminders_on_the_calendar.py` read the markup and
the services. What they cannot say:

- that the menu on a reminder's row actually opens, and that pressing *Put off until next
  week* rewrites the date where it stands, without a page load;
- that the key along the top of the calendar really is the filter, since it being one
  control rather than two is the whole argument for drawing it that way;
- that a reminder's menu on the calendar opens where it can be read -- inside the grid, not
  off the edge of it -- from the first column and from the last;
- that the whole of what the reminders page did is done from the calendar with no script:
  the menu is a popover the browser opens by itself (#310), and every action is a form or
  a link that comes back.

The walk of the reminders page is here too, moved to the calendar with it.
"""

from __future__ import annotations

import datetime as dt

import pytest
from playwright.sync_api import Browser, Page, expect

from tests.e2e.signing_in import sign_in

from .test_accessibility import axe_source, describe, violations_on  # noqa: F401
from .test_reflow import LANGUAGES, SCROLLS_SIDEWAYS, SPILLS

pytestmark = pytest.mark.e2e


@pytest.fixture
def dated(applicant):
    """An application with a deadline, a reminder and a listing that closes."""
    from django.utils import timezone

    from postulo.applications.models import Application, Reminder, Status
    from postulo.jobs.models import Company, JobPosting

    today = timezone.localdate()
    company = Company.objects.create(owner=applicant, name="Aperture Science")
    posting = JobPosting.objects.create(owner=applicant, company=company, title="Test Engineer")
    application = Application.objects.create(
        owner=applicant, posting=posting, status=Status.DRAFT, deadline=today + dt.timedelta(2)
    )
    JobPosting.objects.create(
        owner=applicant,
        company=company,
        title="Portal Researcher",
        closes_at=today + dt.timedelta(days=3),
    )
    reminder = Reminder.objects.create(
        owner=applicant,
        application=application,
        summary="Chase them about the test chamber",
        due_at=timezone.now(),
    )
    return {"application": application, "reminder": reminder}


def test_putting_a_reminder_off_moves_it_where_it_stands(page: Page, live_server, dated):
    """The row stays and its date changes. A reminder put off until next week is still
    outstanding and still this application's; what was wrong with it was the day."""
    from django.utils import formats, timezone

    reminder = dated["reminder"]
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}{dated['application'].get_absolute_url()}")
    block = page.locator("#reminders")
    expect(block).to_contain_text("Chase them")

    block.locator("button[popovertarget]").click()
    block.get_by_role("menuitem", name="Put off until next week").click()

    next_week = timezone.localtime(timezone.now() + dt.timedelta(days=7))
    expect(block).to_contain_text("Chase them")
    # The row writes the day the language writes it: a named format, never one written
    # out (the rule in CONTRIBUTING). A hard-coded "%d" zero-pads the day the app's "j"
    # does not, so this was green only on the days of the month that carry two digits.
    expect(block).to_contain_text(formats.date_format(next_week, "DATE_FORMAT"))
    reminder.refresh_from_db()
    assert reminder.due_at > timezone.now() + dt.timedelta(days=6)


def test_the_menu_leads_to_editing_and_back_to_the_calendar(page: Page, live_server, dated):
    """The reminders page's walk, on the calendar it moved to (#316): the old address lands
    on the agenda narrowed to reminders, the menu opens, *Edit* opens the form, and *Save*
    comes back to the same shape of the calendar with the change in it."""
    base = live_server.url
    sign_in(page, base)
    page.goto(f"{base}/applications/reminders/")
    expect(page).to_have_url(f"{base}/applications/calendar/?view=agenda&kinds=reminder")
    here = page.url

    row = page.locator("li", has=page.locator('[data-event="reminder"]'))
    row.locator("button[popovertarget]").click()
    row.get_by_role("menuitem", name="Edit, or choose another time").click()

    expect(page).to_have_url(
        f"{base}/applications/reminders/{dated['reminder'].pk}/edit/"
        "?next=%2Fapplications%2Fcalendar%2F%3Fview%3Dagenda%26kinds%3Dreminder"
    )
    expect(page.locator("input[name=summary]")).to_have_value("Chase them about the test chamber")
    page.locator("input[name=summary]").fill("Chase them about the second chamber")
    page.get_by_role("button", name="Save").click()

    expect(page).to_have_url(here)
    expect(page.locator('[data-event="reminder"]')).to_contain_text("second chamber")


@pytest.fixture
def spread(applicant):
    """Reminders in the first and the last column of this week, one overdue from before the
    agenda's thirty days, one further ahead than them, and one done."""
    from django.utils import timezone

    from postulo.applications.agenda import week_start
    from postulo.applications.models import Reminder

    first = week_start(timezone.localdate())

    def at(day, hour=10):
        return timezone.make_aware(dt.datetime(day.year, day.month, day.day, hour))

    made = {
        "first": Reminder.objects.create(
            owner=applicant, summary="Ring the recruiter", due_at=at(first)
        ),
        "last": Reminder.objects.create(
            owner=applicant,
            summary="Send the portfolio",
            due_at=at(first + dt.timedelta(days=6)),
        ),
        "overdue": Reminder.objects.create(
            owner=applicant,
            summary="Renew the certificate",
            due_at=timezone.now() - dt.timedelta(days=45),
        ),
        "further": Reminder.objects.create(
            owner=applicant,
            summary="Ask about the review",
            due_at=timezone.now() + dt.timedelta(days=80),
        ),
    }
    done = Reminder.objects.create(
        owner=applicant, summary="Posted the forms", due_at=at(first + dt.timedelta(days=2))
    )
    done.complete()
    made["done"] = done
    return made


def open_menu(page: Page, summary: str):
    """Open the menu beside the reminder called `summary`, where it is drawn, and return
    the menu itself."""
    event = page.locator('[data-event="reminder"]', has_text=summary)
    row = page.locator("li:visible", has=event).last
    row.locator("button[popovertarget]:visible").click()
    menu = row.get_by_role("menu")
    expect(menu).to_be_visible()
    return menu


def inside(inner: dict, outer: dict) -> bool:
    return inner["x"] >= outer["x"] - 1 and inner["x"] + inner["width"] <= (
        outer["x"] + outer["width"] + 1
    )


@pytest.mark.parametrize("width", [1280, 1024])
def test_a_reminders_menu_opens_inside_the_calendar_from_either_edge(
    page: Page, live_server, spread, width
):
    """A menu is wider than a column. From the first column it opens toward the end, from
    the last toward the start, so neither hangs off the edge of the screen (#316)."""
    base = live_server.url
    sign_in(page, base)
    page.set_viewport_size({"width": width, "height": 900})
    screen = {"x": 0, "width": width}

    for view in ("", "?view=week"):
        for which in ("first", "last"):
            page.goto(f"{base}/applications/calendar/{view}")
            menu = open_menu(page, spread[which].summary)
            box = menu.bounding_box()
            assert inside(box, screen), f"{view or 'month'}, {which}: {box} at {width}"
            assert not page.evaluate(SCROLLS_SIDEWAYS)["reached"], f"{view}, {which}"
            page.keyboard.press("Escape")


def test_a_months_menu_can_always_be_reached_inside_its_box_on_a_phone(
    page: Page, live_server, spread
):
    """Below `lg` the month scrolls in its own box, and a menu opens inside the grid it is
    in: never over the start of the table, which nothing could scroll to."""
    base = live_server.url
    sign_in(page, base)
    page.set_viewport_size({"width": 390, "height": 800})
    page.goto(f"{base}/applications/calendar/")
    table = page.locator("[data-calendar=month]")

    for which in ("first", "last"):
        menu = open_menu(page, spread[which].summary)
        grid = table.bounding_box()
        assert inside(menu.bounding_box(), grid), which
        page.keyboard.press("Escape")
    assert not page.evaluate(SCROLLS_SIDEWAYS)["reached"]


def test_everything_the_reminders_page_did_works_on_the_calendar_with_no_script(
    browser: Browser, live_server, spread
):
    """Done, later, a new one on a day, all from the calendar with scripts off: the menu
    is a disclosure the browser opens, and every action comes back to where it was
    pressed."""
    from django.utils import timezone

    base = live_server.url
    context = browser.new_context(java_script_enabled=False)
    page = context.new_page()
    try:
        sign_in(page, base)

        page.goto(f"{base}/applications/calendar/")
        month = page.url
        open_menu(page, spread["first"].summary).get_by_role("menuitem", name="Done").click()
        expect(page).to_have_url(month)
        spread["first"].refresh_from_db()
        assert spread["first"].is_done

        page.goto(f"{base}/applications/calendar/?view=week")
        week = page.url
        open_menu(page, spread["last"].summary).get_by_role(
            "menuitem", name="Put off until next week"
        ).click()
        expect(page).to_have_url(week)
        spread["last"].refresh_from_db()
        assert spread["last"].due_at > timezone.now() + dt.timedelta(days=6)

        page.goto(f"{base}/applications/calendar/?view=agenda&kinds=reminder")
        agenda = page.url
        overdue = page.locator('[data-reminders="overdue"]')
        expect(overdue).to_contain_text(spread["overdue"].summary)
        overdue.get_by_role("button", name="Done").click()
        expect(page).to_have_url(agenda)
        expect(page.locator('[data-reminders="overdue"]')).to_have_count(0)

        day = timezone.localdate() + dt.timedelta(days=1)
        page.goto(f"{base}/applications/calendar/?view=day&on={day:%Y-%m-%d}")
        here = page.url
        name = f"New reminder on {day.day} {day:%B}"
        page.get_by_role("link", name=name).click()
        expect(page.locator("input[name=due_at]")).to_have_value(f"{day:%Y-%m-%d}T09:00")
        page.locator("input[name=summary]").fill("Book the train")
        page.get_by_role("button", name="Save").click()
        expect(page).to_have_url(here)
        made = page.locator('[data-event="reminder"]', has_text="Book the train")
        expect(made).to_have_count(1)
    finally:
        context.close()


#: The shapes of the calendar #316 changed, and the form a day opens.
CHANGED = (
    "/applications/calendar/",
    "/applications/calendar/?view=week",
    "/applications/calendar/?view=day",
    "/applications/calendar/?view=agenda",
    "/applications/calendar/?view=agenda&kinds=reminder",
    "/applications/reminders/new/?on=2026-10-03",
)


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_calendar_with_its_reminders_has_no_violations(
    page: Page,
    live_server,
    spread,
    axe_source,  # noqa: F811
    scheme,
):
    """Every state a reminder is drawn in -- outstanding, done, overdue, further ahead, in
    the first column and the last -- in both themes."""
    page.emulate_media(color_scheme=scheme)
    sign_in(page, live_server.url)
    failures = []
    for path in CHANGED:
        page.goto(f"{live_server.url}{path}")
        found = violations_on(page, axe_source)
        if found:
            failures.append(describe(f"{path} ({scheme})", found))
    assert not failures, "\n\n".join(failures)


@pytest.mark.parametrize("language", LANGUAGES)
def test_the_calendar_with_its_reminders_reflows_at_320_pixels(
    page: Page, live_server, spread, applicant, language
):
    from postulo.accounts.models import Profile

    sign_in(page, live_server.url)
    Profile.objects.filter(user=applicant).update(language=language)
    page.set_viewport_size({"width": 320, "height": 800})
    failures = []
    for path in CHANGED:
        page.goto(f"{live_server.url}{path}")
        reached = page.evaluate(SCROLLS_SIDEWAYS)["reached"]
        if reached:
            failures.append(f"{path} scrolls {reached}px sideways")
        for spill in page.evaluate(SPILLS):
            failures.append(f"{path}: words {spill['needs']}px in {spill['box']}px {spill['what']}")
    assert not failures, "\n".join(failures)


def test_the_calendar_key_is_the_filter(page: Page, live_server, dated):
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/applications/calendar/?view=agenda")

    expect(page.locator('[data-event="deadline"]')).to_have_count(1)
    expect(page.locator('[data-event="reminder"]')).to_have_count(1)
    expect(page.locator('[data-event="closing"]')).to_have_count(1)

    page.locator('[data-legend] [data-kind="reminder"]').click()

    expect(page.locator('[data-event="reminder"]')).to_have_count(0)
    expect(page.locator('[data-event="deadline"]')).to_have_count(1)
    expect(page.locator('[data-legend] [data-kind="reminder"]')).not_to_have_attribute(
        "data-showing", "true"
    )

    page.get_by_role("link", name="Show everything").click()

    expect(page.locator('[data-event="reminder"]')).to_have_count(1)


def test_a_whole_day_draws_no_hour(page: Page, live_server, dated):
    """A deadline is a date. A midnight in front of it would be a fact Postulo invented."""
    sign_in(page, live_server.url)
    page.goto(f"{live_server.url}/applications/calendar/?view=agenda&kinds=deadline")

    entry = page.locator('[data-event="deadline"]')
    expect(entry).to_contain_text("Test Engineer")
    expect(entry).not_to_contain_text("00:00")
