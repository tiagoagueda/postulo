"""What Insights, the report and the salary column actually count (#224).

Each of these is a number somebody reads and believes: the notifier tells them an
application has gone quiet, the Outcomes widget says how many are still waiting, and the
report goes to an employment office. Every one of them was counting something slightly
different from what it said.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from postulo.applications import analytics, quiet
from postulo.applications.models import (
    Application,
    EventKind,
    InterviewKind,
    InterviewOutcome,
    Status,
)
from postulo.applications.services import (
    change_status,
    record_event,
    reschedule_interview,
    schedule_interview,
)
from postulo.core import csv_import
from postulo.jobs.models import Company, JobPosting, SalaryPeriod

pytestmark = pytest.mark.django_db


@pytest.fixture
def company(user):
    return Company.objects.create(owner=user, name="Aperture Science")


def sent(user, company, *, days_ago: int, title="Test Engineer", status=Status.APPLIED):
    posting = JobPosting.objects.create(owner=user, company=company, title=title)
    application = Application.objects.create(owner=user, posting=posting, status=Status.DRAFT)
    when = timezone.now() - dt.timedelta(days=days_ago)
    change_status(application, Status.APPLIED, occurred_at=when)
    if status != Status.APPLIED:
        change_status(application, status, occurred_at=when)
    application.refresh_from_db()
    return application


# ------------------------------------------------- 1. quiet, after an interview


def test_an_interview_waiting_for_its_outcome_is_not_silence(user, company):
    """The morning after an interview, the notifier used to say the application was quiet."""
    application = sent(user, company, days_ago=40)
    schedule_interview(
        application,
        kind=InterviewKind.PHONE,
        starts_at=timezone.now() - dt.timedelta(days=1),
        remind=False,
    )
    # Booked in the past, so it is recorded as held straight away; put it back to waiting,
    # which is where an interview sits between happening and being written up.
    application.interviews.update(outcome=InterviewOutcome.SCHEDULED)

    assert list(quiet.quiet_applications(user)) == [], "waiting on an answer, not gone quiet"


def test_an_interview_that_has_been_written_up_no_longer_holds_it_open(user, company):
    application = sent(user, company, days_ago=40)
    interview = schedule_interview(
        application,
        kind=InterviewKind.PHONE,
        starts_at=timezone.now() - dt.timedelta(days=30),
        remind=False,
    )
    interview.refresh_from_db()
    assert interview.outcome == InterviewOutcome.DONE

    assert [a.pk for a in quiet.quiet_applications(user)] == [application.pk]


def test_an_interview_booked_ahead_still_means_waiting(user, company):
    application = sent(user, company, days_ago=40)
    schedule_interview(
        application,
        kind=InterviewKind.PHONE,
        starts_at=timezone.now() + dt.timedelta(days=21),
        remind=False,
    )

    assert list(quiet.quiet_applications(user)) == []


# ------------------------------------------------------ 2. still waiting for a reply


def test_an_application_you_withdrew_is_not_still_waiting_for_a_reply(user, company):
    sent(user, company, days_ago=30, title="Waiting")
    sent(user, company, days_ago=30, title="Withdrawn", status=Status.WITHDRAWN)
    sent(user, company, days_ago=30, title="Ghosted", status=Status.GHOSTED)

    assert analytics.build(user).still_waiting == 1


# ------------------------------------------------ 3. the report and Insights agree


def test_an_interview_typed_onto_the_timeline_reaches_the_report(user, company):
    application = sent(user, company, days_ago=20)
    record_event(
        application,
        kind=EventKind.INTERVIEW,
        summary="Interview, written down afterwards",
        occurred_at=timezone.now() - dt.timedelta(days=5),
    )

    assert analytics.interviews_held(user) == 1
    assert analytics.build(user).interviews_held == 1


def test_a_diary_interview_is_counted_once_and_not_twice(user, company):
    application = sent(user, company, days_ago=20)
    schedule_interview(
        application,
        kind=InterviewKind.PHONE,
        starts_at=timezone.now() - dt.timedelta(days=2),
        remind=False,
    )

    assert analytics.interviews_held(user) == 1, "held once, counted once"


def test_interviews_are_counted_inside_the_period_only(user, company):
    application = sent(user, company, days_ago=400)
    for days in (2, 400):
        record_event(
            application,
            kind=EventKind.INTERVIEW,
            summary="Interview",
            occurred_at=timezone.now() - dt.timedelta(days=days),
        )
    today = timezone.localdate()

    assert analytics.interviews_held(user, start=today - dt.timedelta(days=30), end=today) == 1
    assert analytics.interviews_held(user) == 2


# ------------------------------------------------------ 4. a moved interview's reminder


def test_moving_an_interview_rewords_its_reminder(user, company):
    application = sent(user, company, days_ago=5)
    interview = schedule_interview(
        application,
        kind=InterviewKind.PHONE,
        starts_at=timezone.now() + dt.timedelta(days=5, hours=2),
    )
    was = interview.reminder.summary

    reschedule_interview(
        interview,
        starts_at=timezone.now() + dt.timedelta(days=6, hours=9),
        ends_at=timezone.now() + dt.timedelta(days=6, hours=10),
    )

    interview.reminder.refresh_from_db()
    assert interview.reminder.summary != was, "it names a time, and the time changed"


# ------------------------------------------------------------------- 5. salaries


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("50-60k", (Decimal(50000), Decimal(60000))),
        ("50k-60k", (Decimal(50000), Decimal(60000))),
        ("50000 - 60000", (Decimal(50000), Decimal(60000))),
        ("45k", (Decimal(45000), Decimal(45000))),
    ],
)
def test_a_trailing_k_belongs_to_both_figures(text, expected):
    assert csv_import.parse_salary_range(text) == expected


def test_the_word_a_splits_a_range_and_the_letter_a_does_not():
    assert csv_import.parse_salary_range("30000 a 40000") == (Decimal(30000), Decimal(40000))
    assert csv_import.parse_salary_range("Salary 45000") == (Decimal(45000), Decimal(45000))


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("£55,000", "GBP"),
        ("$55,000", "USD"),
        ("55 000 €", "EUR"),
        ("55000 CHF", "CHF"),
        ("55000 chf", "CHF"),
        ("55000", ""),
    ],
)
def test_the_currency_is_read_from_the_cell(text, code):
    assert csv_import.parse_currency(text) == code


@pytest.mark.parametrize(
    ("text", "period"),
    [
        ("15 €/h", "hour"),
        ("15 € per hour", "hour"),
        ("3000 €/month", "month"),
        ("250 € a day", "day"),
        ("55000 p.a.", "year"),
        ("55000", ""),
    ],
)
def test_the_period_is_read_from_the_cell(text, period):
    assert csv_import.parse_period(text) == period


def a_sheet(cell: str) -> csv_import.Sheet:
    return csv_import.Sheet(
        headers=["Company", "Role", "Salary"],
        rows=[["Aperture Science", "Test Engineer", cell]],
        filename="jobs.csv",
        delimiter=",",
        encoding="utf-8",
    )


def test_an_imported_salary_keeps_its_currency_and_period(user):
    csv_import.perform(user, a_sheet("£45-55k"), ["company", "role", "salary"])

    posting = JobPosting.objects.get()
    assert (posting.salary_min, posting.salary_max) == (Decimal(45000), Decimal(55000))
    assert posting.salary_currency == "GBP", "the cell said so, whatever the sheet's default"
    assert posting.salary_period == SalaryPeriod.YEAR


def test_a_sheet_that_never_says_uses_the_default_chosen_on_the_mapping_page(user):
    csv_import.perform(user, a_sheet("30 an hour"), ["company", "role", "salary"], currency="SEK")

    posting = JobPosting.objects.get()
    assert posting.salary_currency == "SEK"
    assert posting.salary_period == SalaryPeriod.HOUR, "not a year's pay of thirty"


def test_the_salary_says_which_period_it_is(user, company):
    posting = JobPosting.objects.create(
        owner=user,
        company=company,
        title="Test Engineer",
        salary_min=Decimal(30),
        salary_max=Decimal(40),
        salary_currency="EUR",
        salary_period=SalaryPeriod.HOUR,
    )

    assert "Per hour" in posting.salary_display


def test_a_currency_is_three_letters_upper_cased(user, company):
    posting = JobPosting.objects.create(
        owner=user, company=company, title="Test Engineer", salary_currency="gbp"
    )
    assert posting.salary_currency == "GBP"

    posting.salary_currency = "1UR"
    with pytest.raises(ValidationError):
        posting.full_clean()


def test_the_salary_column_sorts_within_a_currency_by_the_year(user, company):
    def a_posting(title, low, high, currency, period):
        posting = JobPosting.objects.create(
            owner=user,
            company=company,
            title=title,
            salary_min=Decimal(low),
            salary_max=Decimal(high),
            salary_currency=currency,
            salary_period=period,
        )
        return Application.objects.create(owner=user, posting=posting, status=Status.APPLIED)

    a_posting("Hourly", 30, 40, "EUR", SalaryPeriod.HOUR)  # about 67k a year
    a_posting("Yearly", 45000, 50000, "EUR", SalaryPeriod.YEAR)
    a_posting("Pounds", 1, 2, "GBP", SalaryPeriod.YEAR)

    ordered = (
        Application.objects.for_user(user)
        .with_salary_order()
        .order_by("posting__salary_currency", "-salary_year_max")
        .values_list("posting__title", flat=True)
    )

    assert list(ordered) == ["Hourly", "Yearly", "Pounds"], (
        "an hourly rate is not below every annual one, and currencies do not mix"
    )
