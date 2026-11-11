"""Calendars somebody else keeps, in a browser: followed, drawn, and made one's own (#1098).

The unit tests read the pages' markup; what they cannot reach is the pages as a person meets
them. So the path that matters is walked once -- subscribe, find an event on the calendar,
open it and add it as an appointment -- and the four pages it crosses are measured the way
the whole walk measures every page: axe in both themes and right to left, no sideways scroll
at 320 pixels, and targets big enough.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.utils import timezone
from playwright.sync_api import Page, expect

from .test_accessibility import (  # noqa: F401
    axe_source,
    describe,
    furnished,
    sign_in,
    violations_on,
)
from .test_reflow import NARROW, SCROLLS_SIDEWAYS
from .test_target_size import measure_targets

pytestmark = pytest.mark.e2e


def pages(furnished) -> list[str]:  # noqa: F811
    subscription, subscribed = furnished["subscription"], furnished["subscribed"]
    day = timezone.localtime(subscribed.starts_at)
    return [
        "/applications/calendar/subscriptions/",
        "/applications/calendar/subscriptions/new/",
        f"/applications/calendar/subscriptions/{subscription.pk}/edit/",
        f"/applications/calendar/subscribed/{subscribed.pk}/",
        f"/applications/calendar/?view=day&on={day:%Y-%m-%d}",
        f"/applications/calendar/?month={day:%Y-%m}",
    ]


def test_an_event_of_a_followed_calendar_becomes_an_appointment(
    live_server,
    page: Page,
    furnished,  # noqa: F811
):
    from postulo.applications.models import Appointment, CalendarSubscription

    base = live_server.url
    me = furnished["applicant"]
    subscribed = furnished["subscribed"]
    sign_in(page, base)

    page.goto(f"{base}/applications/calendar/")
    page.locator("[data-subscriptions]").click()
    page.locator("[data-new-subscription]").click()
    page.get_by_label("Address").fill("webcal://calendar.example.org/coach/basic.ics")
    page.locator("#id_name").fill("Career coach")
    page.locator("#id_colour").select_option("violet")
    page.get_by_role("button", name="Subscribe").click()

    expect(page.get_by_text("Subscribed.")).to_be_visible()
    made = CalendarSubscription.objects.get(owner=me, name="Career coach")
    assert made.address == "https://calendar.example.org/coach/basic.ics"
    expect(page.locator("main")).not_to_contain_text("coach/basic.ics")

    day = timezone.localtime(subscribed.starts_at)
    page.goto(f"{base}/applications/calendar/?view=day&on={day:%Y-%m-%d}")
    marked = page.locator('[data-event="subscribed"]')
    expect(marked).to_contain_text("Workshop: interviews")
    expect(marked.locator("[data-subscribed]")).to_be_visible()
    marked.click()

    expect(page.locator("[data-subscribed-from]")).to_contain_text("Training centre")
    page.locator("#id_action_0").check()
    page.locator("#id_company").select_option(str(furnished["office"].pk))
    page.get_by_role("button", name="Add", exact=True).click()

    expect(page.get_by_text("Added to your appointments")).to_be_visible()
    added = Appointment.objects.get(owner=me, starts_at=subscribed.starts_at)
    assert added.place == "Room 4" and added.company == furnished["office"]


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_pages_have_no_violations(
    live_server,
    page: Page,
    axe_source,  # noqa: F811
    furnished,  # noqa: F811
    scheme,
):
    page.emulate_media(color_scheme=scheme)
    base = live_server.url
    sign_in(page, base)

    failures = []
    for path in pages(furnished):
        page.goto(f"{base}{path}")
        found = violations_on(page, axe_source)
        if found:
            failures.append(describe(f"{path} ({scheme})", found))
    assert not failures, "\n\n".join(failures)


def test_the_pages_read_right_to_left_without_violations(
    live_server,
    page: Page,
    axe_source,  # noqa: F811
    furnished,  # noqa: F811
):
    profile = furnished["applicant"].profile
    profile.language = "ar"
    profile.save(update_fields=["language"])
    base = live_server.url
    sign_in(page, base)

    failures = []
    for path in pages(furnished):
        page.goto(f"{base}{path}")
        expect(page.locator("html")).to_have_attribute("dir", "rtl")
        found = violations_on(page, axe_source)
        if found:
            failures.append(describe(f"{path} rtl", found))
    assert not failures, "\n\n".join(failures)


def test_the_pages_fit_a_phone_and_their_targets_are_big_enough(
    live_server,
    page: Page,
    furnished,  # noqa: F811
):
    base = live_server.url
    sign_in(page, base)
    page.set_viewport_size({"width": NARROW, "height": 800})

    sideways = {}
    small: dict[str, str] = {}
    for number, path in enumerate(pages(furnished)):
        page.goto(f"{base}{path}")
        reached = page.evaluate(SCROLLS_SIDEWAYS)["reached"]
        if reached:
            sideways[path] = reached
        measure_targets(page, path, small, masthead=number == 0)
    assert not sideways, f"scrolls sideways at {NARROW}px: {sideways}"
    assert not small, small


def test_a_failing_calendar_says_so_on_its_card(live_server, page: Page, furnished):  # noqa: F811
    subscription = furnished["subscription"]
    subscription.last_error = "The calendar's server has nothing at that address any more."
    subscription.fetched_at = timezone.now()
    subscription.created_at = timezone.now() - dt.timedelta(hours=5)
    subscription.save()
    base = live_server.url
    sign_in(page, base)

    page.goto(f"{base}/applications/calendar/subscriptions/")

    card = page.locator(f'[data-subscription="{subscription.pk}"]')
    expect(card.locator("[data-subscription-error]")).to_contain_text("nothing at that address")
    expect(card.locator("[data-subscription-error]")).to_contain_text("will try again")
