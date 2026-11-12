"""Calendars somebody else keeps, followed read-only and read again by the scheduler (#1098).

Postulo published feeds and imported a file once; it could not follow a calendar that keeps
changing. A subscription is an address, a name, a colour and an interval; its events are kept
in a table of their own, replaced on every reading that works, drawn on the calendar marked as
from it, and made the person's own only by hand. Every fetch goes through the guarded client,
and the address, which may be what opens the calendar, is a secret.
"""

from __future__ import annotations

import datetime as dt
import io
import ipaddress
import json
import logging
import zipfile
from types import SimpleNamespace

import httpx
import pytest
from django.urls import reverse
from django.utils import timezone

from postulo.applications import agenda, subscriptions
from postulo.applications.forms import SubscriptionForm
from postulo.applications.models import (
    Appointment,
    CalendarSubscription,
    Interview,
    SubscribedEvent,
)
from postulo.core import export as export_module
from postulo.core import importer
from postulo.jobs.models import Company, CompanyKind, JobPosting
from postulo.plugins import http

pytestmark = pytest.mark.django_db

PUBLIC = [ipaddress.ip_address("185.15.59.224")]
ADDRESS = "https://calendar.example.org/private/s3cr3t-token-42/basic.ics"


def calendar(*components: str) -> bytes:
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Test//EN", *components, "END:VCALENDAR"]
    return ("\r\n".join(lines) + "\r\n").encode()


def event(*props: str, uid: str = "one@example.test") -> str:
    return "\r\n".join(
        ["BEGIN:VEVENT", f"UID:{uid}", "DTSTAMP:20260101T000000Z", *props, "END:VEVENT"]
    )


def stamp(moment: dt.datetime) -> str:
    return moment.astimezone(dt.UTC).strftime("%Y%m%dT%H%M%SZ")


def soon(days: int = 3, hour: int = 10) -> dt.datetime:
    day = timezone.localtime() + dt.timedelta(days=days)
    return day.replace(hour=hour, minute=0, second=0, microsecond=0)


@pytest.fixture
def served(monkeypatch):
    """The calendar's server: whatever `served["body"]` holds, at whatever status, and the
    requests it saw. The destination policy is left on, with the name resolving publicly."""
    state = {"body": calendar(), "status": 200, "seen": []}

    def handler(request: httpx.Request) -> httpx.Response:
        state["seen"].append(request)
        return httpx.Response(state["status"], content=state["body"])

    monkeypatch.setattr(http, "public_addresses_for", lambda url: PUBLIC)
    real = http.client
    monkeypatch.setattr(
        http, "client", lambda **kwargs: real(transport=httpx.MockTransport(handler), **kwargs)
    )
    return state


@pytest.fixture
def subscription(user):
    made = CalendarSubscription(owner=user, name="Training centre", colour="teal")
    made.address = ADDRESS
    made.save()
    return made


def an_event(subscription, **values) -> SubscribedEvent:
    values.setdefault("starts_at", soon())
    values.setdefault("summary", "Workshop: interviews")
    return SubscribedEvent.objects.create(subscription=subscription, **values)


# ------------------------------------------------------------------ on the calendar


def test_its_events_are_drawn_on_the_calendar_marked_as_from_it(client, user, subscription):
    """The calendar showed only the person's own records; a subscribed calendar's events had
    nowhere to appear (#1098). They are drawn in the subscription's colour, marked to the eye
    and in words as from it, and link to a page of their own rather than to a form."""
    when = soon()
    held = an_event(subscription, starts_at=when, location="Room 4")
    client.force_login(user)

    page = client.get(
        reverse("applications:calendar") + f"?view=day&on={when:%Y-%m-%d}"
    ).content.decode()

    assert "Workshop: interviews" in page
    assert 'data-event="subscribed"' in page and "data-subscribed" in page
    assert "cal-teal" in page
    assert "From the subscribed calendar Training centre:" in page
    assert held.get_absolute_url() in page
    assert 'data-kind="subscribed"' in page, "the legend offers them to somebody who has some"


def test_the_legend_does_not_offer_them_to_somebody_without_any(user):
    page = agenda.build(user, "month", timezone.localdate())

    assert agenda.SUBSCRIBED not in {kind for kind, *_rest in page.legend}


def test_a_whole_day_is_drawn_on_its_day_and_a_cancelled_one_is_struck(user, subscription):
    day = timezone.localdate() + dt.timedelta(days=4)
    an_event(subscription, summary="Open day", day=day, starts_at=soon(4, 0))
    an_event(subscription, summary="Called off", cancelled=True, starts_at=soon(4, 15))

    events = agenda.events_between(user, day, day + dt.timedelta(days=1), [agenda.SUBSCRIBED])

    by_title = {event.title: event for event in events}
    assert by_title["Open day"].all_day and by_title["Open day"].day == day
    assert by_title["Called off"].muted
    assert by_title["Called off"].said.startswith("Cancelled, from the subscribed calendar")


def test_nobody_else_sees_them(client, other_user, subscription):
    held = an_event(subscription)
    client.force_login(other_user)

    events = agenda.events_between(
        other_user, timezone.localdate(), timezone.localdate() + dt.timedelta(days=10)
    )

    assert not [event for event in events if event.kind == agenda.SUBSCRIBED]
    assert client.get(held.get_absolute_url()).status_code == 404
    for name in ("subscription_update", "subscription_delete"):
        url = reverse(f"applications:{name}", args=[subscription.pk])
        assert client.get(url).status_code == 404
    refresh = reverse("applications:subscription_refresh", args=[subscription.pk])
    assert client.post(refresh).status_code == 404


def test_the_file_and_a_plugin_do_not_carry_them(user, subscription):
    """They are the keeper's, not the person's: the page's download and a calendar plugin's
    reading leave them out, and a plugin cannot ask for them by name."""
    when = soon()
    an_event(subscription, starts_at=when)
    start, end = when.date(), when.date() + dt.timedelta(days=1)

    events = agenda.events_between(user, start, end)
    assert any(event.kind == agenda.SUBSCRIBED for event in events)
    assert "Workshop" not in agenda.download_for(events, lambda path: f"https://x.test{path}")
    assert not [item for item in agenda.items_between(user, start, end) if not item.uid]
    with pytest.raises(ValueError):
        agenda.items_between(user, start, end, kinds=[agenda.SUBSCRIBED])


# ------------------------------------------------------------------ reading it


def test_a_reading_replaces_what_it_held_and_says_what_it_left_out(user, subscription, served):
    an_event(subscription, summary="Gone since")
    when = soon()
    served["body"] = calendar(
        event(
            f"DTSTART:{stamp(when)}",
            f"DTEND:{stamp(when + dt.timedelta(hours=2))}",
            "SUMMARY:CV clinic",
            "LOCATION:Room 2",
        ),
        event("DTSTART:20261201T090000Z", "RRULE:FREQ=WEEKLY", "SUMMARY:Weekly", uid="r@x"),
        event("DTSTART:20261201T090000", "SUMMARY:Floating", uid="f@x"),
        "BEGIN:VTODO\r\nUID:t@x\r\nSUMMARY:A task\r\nEND:VTODO",
    )

    assert subscriptions.refresh(subscription)

    [kept] = SubscribedEvent.objects.filter(subscription=subscription)
    assert (kept.summary, kept.location, kept.starts_at) == ("CV clinic", "Room 2", when)
    subscription.refresh_from_db()
    assert subscription.last_ok_at and not subscription.last_error
    lines = subscriptions.left_out_lines(subscription)
    assert lines == [
        "1 repeating event: Postulo does not read repeating events.",
        "1 event with a time but no time zone Postulo knows.",
        "1 task.",
    ]
    [request] = served["seen"]
    assert request.headers["Host"] == "calendar.example.org"


def test_a_failed_reading_keeps_what_it_held_and_names_no_address(
    user, subscription, served, caplog
):
    an_event(subscription, summary="Still here")
    served["status"] = 403

    with caplog.at_level(logging.DEBUG):
        assert not subscriptions.refresh(subscription)

    subscription.refresh_from_db()
    assert "refused the address" in subscription.last_error
    assert SubscribedEvent.objects.filter(subscription=subscription).count() == 1
    assert "s3cr3t" not in caplog.text and "s3cr3t" not in subscription.last_error


def test_a_private_address_is_refused_unless_the_operator_allows_it(user, settings):
    """As every outbound request: the form refuses it before it is kept, and the client
    refuses it again on every fetch, redirects included."""
    data = {"address": "http://192.168.1.20/calendar.ics", "name": "Home", "colour": "grey"}
    data["interval"] = "60"

    form = SubscriptionForm(data, user=user)
    assert not form.is_valid()
    assert "POSTULO_CONNECTIONS_ALLOW_PRIVATE" in str(form.errors["address"])

    settings.POSTULO_CONNECTIONS_ALLOW_PRIVATE = True
    assert SubscriptionForm(data, user=user).is_valid()


def test_the_fetch_is_refused_at_a_private_address(user, monkeypatch):
    private = CalendarSubscription(owner=user, name="Router")
    private.address = "http://127.0.0.1:8080/calendar.ics"
    private.save()

    assert not subscriptions.refresh(private)
    private.refresh_from_db()
    assert "private or local network" in private.last_error


def test_a_webcal_address_is_read_over_https():
    assert subscriptions.normalise(" webcal://example.org/a.ics ") == "https://example.org/a.ics"
    with pytest.raises(ValueError):
        subscriptions.normalise("ftp://example.org/a.ics")


# ------------------------------------------------------------------ the address is a secret


def test_the_address_is_kept_encrypted_and_shown_as_its_site(client, user, subscription):
    stored = CalendarSubscription.objects.values_list("address_encrypted", flat=True).get()
    assert "s3cr3t" not in stored and subscription.address == ADDRESS
    client.force_login(user)

    listing = client.get(reverse("applications:subscription_list")).content.decode()
    form = client.get(
        reverse("applications:subscription_update", args=[subscription.pk])
    ).content.decode()

    assert "https://calendar.example.org/…" in listing
    assert "s3cr3t" not in listing and "s3cr3t" not in form


def test_leaving_the_address_empty_keeps_it(user, subscription):
    form = SubscriptionForm(
        {"address": "", "name": "Renamed", "colour": "rose", "interval": "240"},
        instance=subscription,
        user=user,
    )
    assert form.is_valid(), form.errors
    form.save()

    subscription.refresh_from_db()
    assert (subscription.name, subscription.address) == ("Renamed", ADDRESS)


# ------------------------------------------------------------------ the scheduler


def test_each_is_read_once_per_interval_and_less_often_while_it_fails(
    user, other_user, subscription, served
):
    served["body"] = calendar(event(f"DTSTART:{stamp(soon())}", "SUMMARY:One"))

    assert subscriptions.refresh_due() == (1, 0)
    assert subscriptions.refresh_due() == (0, 0), "not again before its interval"

    later = timezone.now() + dt.timedelta(minutes=61)
    assert [due.pk for due in subscriptions.due(later)] == [subscription.pk]

    # Failing since it was made a day and a half ago: it waits as long, up to a day.
    served["status"] = 500
    CalendarSubscription.objects.filter(pk=subscription.pk).update(
        created_at=timezone.now() - dt.timedelta(hours=36),
        last_ok_at=None,
        fetched_at=timezone.now() - dt.timedelta(hours=2),
    )
    subscription.refresh_from_db()
    assert subscriptions.refresh_due() == (1, 1)
    subscription.refresh_from_db()
    waits = subscriptions.wait_after_last_read(subscription)
    assert waits == subscriptions.LONGEST_WAIT
    assert subscriptions.next_attempt(subscription) is not None
    assert not subscriptions.due(timezone.now() + dt.timedelta(hours=23))


def test_a_pass_stops_starting_readings_when_its_budget_is_spent(user, monkeypatch):
    for name in ("One", "Two"):
        made = CalendarSubscription(owner=user, name=name)
        made.address = ADDRESS
        made.save()
    clock = {"now": 0.0}

    def slow_reading(subscription) -> bool:
        clock["now"] += 100.0
        return True

    monkeypatch.setattr(subscriptions, "time", SimpleNamespace(monotonic=lambda: clock["now"]))
    monkeypatch.setattr(subscriptions, "refresh", slow_reading)

    assert subscriptions.refresh_due(budget=60) == (1, 0)
    assert len(subscriptions.due()) == 1, "the other waits for the next pass, still due"


def test_the_card_says_why_and_when_it_tries_again(client, user, subscription, served):
    served["status"] = 404
    subscriptions.refresh(subscription)
    client.force_login(user)

    page = client.get(reverse("applications:subscription_list")).content.decode()

    assert "nothing at that address any more" in page
    assert "will try again" in page


# ------------------------------------------------------------------ making one the person's own


def test_an_event_can_be_made_an_appointment_which_is_then_the_persons(client, user, subscription):
    office = Company.objects.create(
        owner=user, name="France Travail", kind=CompanyKind.EMPLOYMENT_SERVICE
    )
    held = an_event(subscription, uid="meeting@ft.example", location="Agence Lyon")
    client.force_login(user)

    response = client.post(held.get_absolute_url(), {"action": "appointment", "company": office.pk})

    [made] = Appointment.objects.filter(owner=user)
    assert response.status_code == 302 and made.company == office
    assert (made.starts_at, made.place) == (held.starts_at, "Agence Lyon")
    page = client.get(held.get_absolute_url()).content.decode()
    assert "already in your calendar" in page, "it is not offered twice"


def test_an_event_can_be_made_an_interview_on_an_application(client, user, subscription):
    from postulo.applications.models import Application

    company = Company.objects.create(owner=user, name="Initech")
    posting = JobPosting.objects.create(owner=user, company=company, title="Engineer")
    application = Application.objects.create(owner=user, posting=posting)
    held = an_event(subscription)
    client.force_login(user)

    client.post(
        held.get_absolute_url(),
        {"action": "interview", "application": application.pk, "kind": "video"},
    )

    [made] = Interview.objects.filter(application=application)
    assert made.starts_at == held.starts_at
    assert SubscribedEvent.objects.filter(pk=held.pk).exists(), "the event itself stays"


# ------------------------------------------------------------------ the archive and erasure


def test_the_archive_carries_them_without_the_address(user, other_user, subscription):
    an_event(subscription, uid="a@x", location="Room 1")

    buffer = export_module.write_archive(user)
    archive = zipfile.ZipFile(buffer)
    text = archive.read(export_module.MANIFEST_NAME).decode()
    document = json.loads(text)
    report = importer.load(other_user, archive)

    assert "s3cr3t" not in text and "calendar.example.org" not in text
    [written] = document["calendar_subscriptions"]
    assert written["name"] == "Training centre" and written["events"][0]["uid"] == "a@x"
    assert document["counts"]["calendar_subscriptions"] == 1
    assert export_module.counts(user)["calendar_subscriptions"] == 1
    assert report.calendar_subscriptions == 1
    [restored] = CalendarSubscription.objects.filter(owner=other_user)
    assert (restored.colour, restored.address) == ("teal", "")
    assert restored.events.get().location == "Room 1"
    assert not subscriptions.refresh(restored)
    restored.refresh_from_db()
    assert "Enter it again" in restored.last_error


@pytest.mark.parametrize("older", [80, 81])
def test_an_archive_from_before_them_still_loads(user, other_user, subscription, older):
    """82 is the format that carries them; 81 added the quiet days (#1099) and 80 neither.
    An archive of either is read as it was written, and restores no subscription (#1098)."""
    an_event(subscription)
    document = export_module.build_document(user)
    assert document["postulo"]["format"] == export_module.FORMAT_VERSION >= 82
    del document["calendar_subscriptions"]
    del document["counts"]["calendar_subscriptions"]
    if older < 81:
        for name in ("quiet_days_by_status", "quiet_days_by_channel"):
            document["account"]["profile"].pop(name, None)
    document["postulo"]["format"] = older
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(export_module.MANIFEST_NAME, json.dumps(document))
    buffer.seek(0)

    report = importer.load(other_user, zipfile.ZipFile(buffer))

    assert report.calendar_subscriptions == 0
    assert not CalendarSubscription.objects.filter(owner=other_user).exists()


def test_they_go_with_the_account(user, subscription):
    from postulo.accounts import deletion

    an_event(subscription)
    deletion.delete_account(user)

    assert not CalendarSubscription.objects.exists() and not SubscribedEvent.objects.exists()
