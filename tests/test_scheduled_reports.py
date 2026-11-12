"""Last month's report, made on the day the person chose (#1095).

> make last month's report on a chosen day, keep it with what was sent, and optionally hand
> it on

The period report is what a person hands an employment office each month, and they made it by
hand. These hold the scheduler to what the issue decided: it is *prepared* by default -- filed
under Sent documents, given to no store and mailed to nobody, with a reminder to read it --
and handed on only behind a second switch. A day of the month, or some days before an entry a
plugin placed on the calendar, sets when; and each month is made once, however many schedulers
go round.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from postulo.accounts.models import Profile, ReportSchedule
from postulo.applications import reports, scheduled_reports
from postulo.applications.models import Reminder
from postulo.applications.services import add_reminder
from postulo.documents.models import DocumentCopy, DocumentKind, RenderedDocument
from postulo.plugins import registry
from postulo.plugins.base import ExternalRef
from postulo.plugins.models import Connection

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _a_fresh_hour():
    """The gate is a key in the cache every test shares: none of them inherits another's."""
    cache.delete(scheduled_reports.GATE)
    yield
    cache.delete(scheduled_reports.GATE)


@pytest.fixture
def drawn(monkeypatch):
    """A renderer that draws nothing, so these run where WeasyPrint is not installed."""
    monkeypatch.setattr(
        "postulo.documents.rendering.html_to_pdf", lambda html, backend=None: b"%PDF-1.7 fake"
    )


class Shelf:
    """A store that says yes and remembers nothing."""

    name = "shelf-1095"
    version = "0.1"
    kind = "store"
    label = "Shelf"

    def config_fields(self):
        return []

    def test(self, config):
        from postulo.plugins.base import TestResult

        return TestResult(True, "shelved")

    def put(self, document, file, metadata, config, user):
        return ExternalRef(store=self.name, id="1", url="https://shelf.example/1")


@pytest.fixture
def store(user):
    registry.register_builtin("store", Shelf)
    try:
        yield Connection.objects.create(
            owner=user, kind="store", plugin=Shelf.name, label="My shelf", config={}
        )
    finally:
        registry.unregister_builtin("store", Shelf)


def choose(user, **values) -> Profile:
    Profile.objects.filter(user=user).update(**values)
    user.profile.refresh_from_db()
    return user.profile


def copies_of(document):
    return DocumentCopy.objects.filter(
        document_type=ContentType.objects.get_for_model(document), document_id=document.pk
    )


def last_month_start() -> dt.date:
    return scheduled_reports.last_month(timezone.localdate()).start


# ----------------------------------------------------------------------- when


def test_nobody_is_made_a_report_they_did_not_ask_for(user, drawn):
    assert scheduled_reports.make_due() == 0
    assert not RenderedDocument.objects.exists()


@pytest.mark.parametrize(
    ("today", "due"),
    [
        (dt.date(2026, 11, 4), False),
        (dt.date(2026, 11, 5), True),
        (dt.date(2026, 11, 20), True),  # a scheduler that was stopped on the fifth catches up
    ],
)
def test_a_day_of_the_month_is_due_from_that_day_on(user, today, due):
    profile = choose(user, report_schedule=ReportSchedule.DAY, report_day=5)

    period = scheduled_reports.due_period(profile, today=today)

    assert (period == reports.month_period(2026, 10)) is due


def test_a_day_a_month_does_not_have_is_its_last_day(user):
    profile = choose(user, report_schedule=ReportSchedule.DAY, report_day=31)

    assert scheduled_reports.due_period(profile, today=dt.date(2026, 2, 27)) is None
    assert scheduled_reports.due_period(profile, today=dt.date(2026, 2, 28)) == (
        reports.month_period(2026, 1)
    )


def test_a_month_already_made_is_not_made_again(user):
    profile = choose(
        user,
        report_schedule=ReportSchedule.DAY,
        report_day=1,
        report_made_for=dt.date(2026, 10, 1),
    )

    assert scheduled_reports.due_period(profile, today=dt.date(2026, 11, 20)) is None
    assert scheduled_reports.due_period(profile, today=dt.date(2026, 12, 1)) == (
        reports.month_period(2026, 11)
    )


def test_a_plugin_s_calendar_entry_can_set_the_day(user):
    """A monthly declaration placed by a plugin, and the report some days before it (#1095)."""
    add_reminder(
        user,
        summary="Monthly declaration",
        due_at=timezone.make_aware(dt.datetime(2026, 11, 28, 9, 0)),
        uid="declaration-2026-11",
        source="francetravail",
    )
    profile = choose(
        user,
        report_schedule=ReportSchedule.ENTRY,
        report_entry_source="francetravail",
        report_days_before=3,
    )

    assert scheduled_reports.due_period(profile, today=dt.date(2026, 11, 24)) is None
    assert scheduled_reports.due_period(profile, today=dt.date(2026, 11, 25)) == (
        reports.month_period(2026, 10)
    )


def test_another_plugin_s_entries_and_retired_ones_set_nothing(user):
    when = timezone.make_aware(dt.datetime(2026, 11, 26, 9, 0))
    add_reminder(user, summary="Payment", due_at=when, uid="pay", source="bank")
    add_reminder(user, summary="Declaration", due_at=when, uid="decl", source="francetravail")
    Reminder.objects.filter(uid="decl").update(retired_at=timezone.now())
    profile = choose(
        user,
        report_schedule=ReportSchedule.ENTRY,
        report_entry_source="francetravail",
        report_days_before=3,
    )

    assert scheduled_reports.due_period(profile, today=dt.date(2026, 11, 25)) is None


# --------------------------------------------------------------- prepared only


def test_a_report_made_for_the_person_is_filed_kept_back_and_a_reminder_left(user, drawn, store):
    """Prepared by default: under Sent documents, with no copy queued for any store, and a
    reminder to read it that the next pass announces (#1095)."""
    choose(user, report_schedule=ReportSchedule.DAY, report_day=1)

    assert scheduled_reports.make_due() == 1

    filed = RenderedDocument.objects.for_user(user).get()
    assert filed.kind == DocumentKind.REPORT
    assert filed.title.startswith("Job search report")
    assert not copies_of(filed).exists(), "nobody has read it, so no store has it"
    reminder = Reminder.objects.get(owner=user)
    assert reminder.summary.startswith("Read your job search report for")
    assert reminder.due_at <= timezone.now() and reminder.application is None
    user.profile.refresh_from_db()
    assert user.profile.report_made_for == last_month_start()


def test_send_now_hands_on_a_report_that_was_kept_back(client, user, drawn, store):
    """Once it has been read, the document's own button gives the stores their copy."""
    choose(user, report_schedule=ReportSchedule.DAY, report_day=1)
    scheduled_reports.make_due()
    filed = RenderedDocument.objects.get()
    client.force_login(user)

    client.post(reverse("documents:rendered_archive", args=[filed.pk]))

    assert copies_of(filed).filter(status="sent").exists()


def test_each_month_is_made_once_however_many_passes_go_round(user, drawn):
    choose(user, report_schedule=ReportSchedule.DAY, report_day=1)

    assert scheduled_reports.make_due() == 1
    assert scheduled_reports.make_due() == 0

    assert RenderedDocument.objects.count() == 1
    assert Reminder.objects.count() == 1


def test_a_second_scheduler_that_read_the_same_profile_claims_nothing(user):
    profile = choose(user, report_schedule=ReportSchedule.DAY, report_day=1)
    period = scheduled_reports.last_month(timezone.localdate())
    stale = Profile.objects.get(pk=profile.pk)

    assert scheduled_reports.claim(profile, period)
    assert not scheduled_reports.claim(stale, period), "the value it read has changed"


def test_a_deactivated_account_is_made_nothing(user, drawn):
    choose(user, report_schedule=ReportSchedule.DAY, report_day=1)
    type(user).objects.filter(pk=user.pk).update(is_active=False)

    assert scheduled_reports.make_due() == 0


def test_a_report_that_could_not_be_made_says_so_on_the_calendar(user, monkeypatch):
    from postulo.documents.pdf import PDFBackendUnavailable

    def refuse(html, backend=None):
        raise PDFBackendUnavailable("No PDF renderer is installed.")

    monkeypatch.setattr("postulo.documents.rendering.html_to_pdf", refuse)
    choose(user, report_schedule=ReportSchedule.DAY, report_day=1)

    scheduled_reports.make_due()

    reminder = Reminder.objects.get(owner=user)
    assert "could not be made" in reminder.summary
    assert "No PDF renderer" in reminder.note
    assert not RenderedDocument.objects.exists()


def test_the_pass_looks_at_most_once_an_hour(user, drawn, other_user):
    choose(user, report_schedule=ReportSchedule.DAY, report_day=1)
    assert scheduled_reports.make_if_due() == 1

    choose(other_user, report_schedule=ReportSchedule.DAY, report_day=1)
    assert scheduled_reports.make_if_due() == 0, "the hour is not over"

    cache.delete(scheduled_reports.GATE)  # what the cache does by itself, an hour on
    assert scheduled_reports.make_if_due() == 1


def test_the_scheduler_s_pass_makes_it(user, drawn):
    choose(user, report_schedule=ReportSchedule.DAY, report_day=1)

    call_command("send_due_reminders")

    assert RenderedDocument.objects.for_user(user).filter(kind=DocumentKind.REPORT).exists()


# ------------------------------------------------------------------- handed on


def test_handing_it_on_gives_the_stores_a_copy_and_mails_the_address(
    user, drawn, store, monkeypatch
):
    from postulo.core import correspondence

    mailed = []
    monkeypatch.setattr(correspondence, "send", lambda person, message: mailed.append(message) or 1)
    choose(
        user,
        report_schedule=ReportSchedule.DAY,
        report_day=1,
        report_send=True,
        report_send_to="adviser@example.org",
    )

    scheduled_reports.make_due()

    filed = RenderedDocument.objects.get()
    assert copies_of(filed).exists(), "a copy waits for the store"
    assert [message.to for message in mailed] == [["adviser@example.org"]]
    assert mailed[0].attachments[0][2] == "application/pdf"
    assert "emailed to adviser@example.org" in Reminder.objects.get().note


def test_a_mail_that_did_not_go_is_said_on_the_reminder(user, drawn):
    """No outbox: the report is still filed, and the reminder says it was not emailed."""
    choose(
        user,
        report_schedule=ReportSchedule.DAY,
        report_day=1,
        report_send=True,
        report_send_to="adviser@example.org",
    )

    scheduled_reports.make_due()

    assert RenderedDocument.objects.exists()
    assert "not emailed" in Reminder.objects.get().note


# -------------------------------------------------------------------- the form


def test_the_report_page_offers_the_schedule(client, user):
    client.force_login(user)

    html = client.get(reverse("applications:report")).content.decode()

    assert reverse("applications:report_schedule") in html
    assert 'name="report_schedule"' in html


def test_saving_the_schedule_comes_back_to_the_period(client, user):
    client.force_login(user)

    response = client.post(
        reverse("applications:report_schedule") + "?period=month&on=2026-03",
        {"report_schedule": "day", "report_day": "5", "report_days_before": "3"},
    )

    assert response.status_code == 302
    assert response.url.endswith("?period=month&on=2026-03")
    user.profile.refresh_from_db()
    assert (user.profile.report_schedule, user.profile.report_day) == ("day", 5)
    assert user.profile.report_send is False, "handing it on stays off unless chosen"


def test_an_entry_needs_whose_entries_they_are(client, user):
    client.force_login(user)

    response = client.post(
        reverse("applications:report_schedule"),
        {"report_schedule": "entry", "report_day": "1", "report_days_before": "3"},
    )

    assert response.status_code == 200
    assert "No plugin has put an entry on your calendar yet" in response.content.decode()
    user.profile.refresh_from_db()
    assert user.profile.report_schedule == ""


def test_the_plugins_offered_are_those_with_entries_on_the_calendar(user):
    from postulo.applications.forms import ReportScheduleForm

    add_reminder(
        user, summary="Declaration", due_at=timezone.now(), uid="d", source="francetravail"
    )

    form = ReportScheduleForm(instance=user.profile, user=user)

    assert [value for value, _label in form.fields["report_entry_source"].choices] == [
        "",
        "francetravail",
    ]


def test_an_address_needs_an_outbox_to_send_from(client, user):
    client.force_login(user)

    response = client.post(
        reverse("applications:report_schedule"),
        {
            "report_schedule": "day",
            "report_day": "1",
            "report_days_before": "3",
            "report_send": "on",
            "report_send_to": "adviser@example.org",
        },
    )

    assert response.status_code == 200
    assert "You have no outbox set up" in response.content.decode()


# --------------------------------------------------------------- the archive


def test_the_schedule_travels_with_the_account(user):
    from postulo.core.export import FORMAT_VERSION, PROFILE_FIELDS
    from postulo.core.importer import _REFUSED, _profile_value

    assert FORMAT_VERSION >= 83
    for name in (
        "report_schedule",
        "report_day",
        "report_entry_source",
        "report_days_before",
        "report_send",
        "report_send_to",
        "report_made_for",
    ):
        assert name in PROFILE_FIELDS

    profile = user.profile
    assert _profile_value(profile, "report_schedule", "entry") == "entry"
    assert _profile_value(profile, "report_schedule", "weekly") is _REFUSED
    assert _profile_value(profile, "report_day", 40) is _REFUSED
    assert _profile_value(profile, "report_send_to", "not an address") is _REFUSED
    assert _profile_value(profile, "report_made_for", "2026-10-01") == dt.date(2026, 10, 1)
    assert _profile_value(profile, "report_made_for", "2026-10-15") is _REFUSED
