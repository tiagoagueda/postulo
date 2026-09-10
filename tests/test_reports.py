"""A report about a period: what was sent, how regularly, and what came back (#56).

> be able to generate reports to show regularity of job search

Two people ask for it. An **employment office** makes benefit conditional on actually
looking and wants evidence: what was applied for, where, when, and what came of it. The
**person searching** wants to know whether a month that felt like nothing was in fact eleven
applications across four weeks, and where the gap was.

What the tests here hold to is the promise the issue makes twice — *say nothing that is not
true*. No report mixes in activity from outside its period, no figure is invented, and the
two questions the page answers are kept apart rather than added together.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.urls import reverse
from django.utils import timezone

from postulo.applications import reports
from postulo.applications.models import Application, EventKind, Interview, InterviewOutcome, Status
from postulo.applications.services import change_status, record_event
from postulo.jobs.models import Company, JobPosting

pytestmark = pytest.mark.django_db

PAGE = "applications:report"
CSV = "applications:report_csv"
PDF = "applications:report_pdf"


def sent(user, *, on: dt.date, company="Aperture Science", role="Research Engineer", source=""):
    """One application, sent on that day. The unit everything here is counted in."""
    firm, _made = Company.objects.get_or_create(owner=user, name=company)
    posting = JobPosting.objects.create(
        owner=user, company=firm, title=role, source=source, url="https://example.org/1"
    )
    when = timezone.make_aware(dt.datetime.combine(on, dt.time(10, 0)))
    application = Application.objects.create(
        owner=user, posting=posting, status=Status.DRAFT, applied_at=when
    )
    change_status(application, Status.APPLIED, occurred_at=when)
    application.refresh_from_db()
    Application.objects.filter(pk=application.pk).update(applied_at=when)
    application.refresh_from_db()
    return application


def march() -> reports.Period:
    return reports.month_period(2026, 3)


# ------------------------------------------------------------------- the period


def test_a_month_runs_from_the_first_to_the_last_day():
    period = reports.month_period(2026, 2)

    assert period.start == dt.date(2026, 2, 1)
    assert period.end == dt.date(2026, 2, 28), "and February knows how long it is"
    assert period.days == 28


def test_both_ends_are_inside_it():
    """That is how a person says it, and a report whose last day is silently missing is
    evidence of the wrong thing.
    """
    period = march()

    assert period.holds(dt.date(2026, 3, 1))
    assert period.holds(dt.date(2026, 3, 31))
    assert not period.holds(dt.date(2026, 4, 1))


def test_a_quarter_is_three_months():
    period = reports.quarter_period(2026, 3)

    assert (period.start, period.end) == (dt.date(2026, 7, 1), dt.date(2026, 9, 30))
    assert period.kind == "quarter"


def test_the_last_few_weeks_include_the_one_in_progress():
    """A person asking on a Wednesday how the last month went means including this week."""
    today = dt.date(2026, 3, 18)

    period = reports.weeks_period(4, today=today)

    assert period.holds(today)
    assert period.days == 28


def test_a_month_before_a_month_and_a_quarter_before_a_quarter():
    assert march().shifted(-1).start == dt.date(2026, 2, 1)
    assert march().shifted(1).start == dt.date(2026, 4, 1)
    assert reports.quarter_period(2026, 1).shifted(-1).start == dt.date(2025, 10, 1)


def test_a_range_given_by_hand_moves_by_its_own_length():
    period = reports.Period(dt.date(2026, 3, 10), dt.date(2026, 3, 19), "custom")

    assert period.shifted(-1).start == dt.date(2026, 2, 28)
    assert period.shifted(-1).days == period.days, "the same size, whatever it is"


def test_an_address_nobody_could_have_written_still_gets_a_period():
    """A link that has outlived its shape gets a sensible page rather than an error."""
    period = reports.period_from(
        {"period": "nonsense", "on": "bananas"}, today=dt.date(2026, 3, 18)
    )

    assert period.kind == "month"
    assert period.start == dt.date(2026, 3, 1)


def test_a_quarter_can_be_asked_for_by_a_month_in_it():
    """The page has one date control, and choosing *a quarter* beside September means the
    quarter September is in. A second control ignored three times out of four is worse than
    none, and a quarter that silently ignored what was typed is worse than that.
    """
    period = reports.period_from({"period": "quarter", "on": "2026-09"})

    assert (period.start, period.end) == (dt.date(2026, 7, 1), dt.date(2026, 9, 30))


def test_a_quarter_written_out_still_works():
    """A bookmark, or a link in somebody's notes."""
    period = reports.period_from({"period": "quarter", "on": "2026-Q1"})

    assert period.start == dt.date(2026, 1, 1)


def test_a_range_given_backwards_is_read_forwards():
    period = reports.period_from({"period": "custom", "from": "2026-03-31", "to": "2026-03-01"})

    assert (period.start, period.end) == (dt.date(2026, 3, 1), dt.date(2026, 3, 31))


def test_a_range_of_years_is_cut_short():
    """Not a limit on ambition: five years of weekly bars is a page nobody reads."""
    period = reports.period_from({"period": "custom", "from": "2020-01-01", "to": "2030-01-01"})

    assert period.days == reports.MAX_DAYS


def test_the_query_that_asks_for_this_period_again():
    assert reports.as_query(march()) == {"period": "month", "on": "2026-03"}
    assert reports.as_query(reports.quarter_period(2026, 2))["on"] == "2026-Q2"


# ------------------------------------------------------------------ what is in


def test_nothing_from_outside_the_period_is_counted(user):
    sent(user, on=dt.date(2026, 2, 27))
    sent(user, on=dt.date(2026, 3, 4))
    sent(user, on=dt.date(2026, 4, 1))

    report = reports.build(user, march(), today=dt.date(2026, 3, 31))

    assert report.total == 1
    assert report.evidence[0].applied_on == dt.date(2026, 3, 4)


def test_a_draft_never_sent_is_not_in_it(user):
    """An employment office is asking what was sent, and the page says so."""
    firm = Company.objects.create(owner=user, name="Black Mesa")
    posting = JobPosting.objects.create(owner=user, company=firm, title="Research Engineer")
    Application.objects.create(owner=user, posting=posting, status=Status.DRAFT)
    sent(user, on=dt.date(2026, 3, 4))

    report = reports.build(user, march(), today=dt.date(2026, 3, 31))

    assert report.total == 1


def test_the_drafts_left_out_are_counted_so_the_page_can_say_so(user):
    firm = Company.objects.create(owner=user, name="Black Mesa")
    posting = JobPosting.objects.create(owner=user, company=firm, title="Research Engineer")
    draft = Application.objects.create(owner=user, posting=posting, status=Status.DRAFT)
    Application.objects.filter(pk=draft.pk).update(
        created_at=timezone.make_aware(dt.datetime(2026, 3, 5, 9, 0))
    )

    report = reports.build(user, march(), today=dt.date(2026, 3, 31))

    assert report.drafts == 1


def test_one_person_never_sees_another(user, other_user):
    sent(other_user, on=dt.date(2026, 3, 4))

    assert reports.build(user, march(), today=dt.date(2026, 3, 31)).total == 0


# ------------------------------------------------------------------ the cadence


def test_a_week_with_none_is_a_week_with_none(user):
    sent(user, on=dt.date(2026, 3, 2))
    sent(user, on=dt.date(2026, 3, 3))

    cadence = reports.build(user, march(), today=dt.date(2026, 3, 31)).cadence

    counts = [week.count for week in cadence.weeks]
    assert 2 in counts
    assert cadence.empty_weeks >= 1
    assert any(week.empty for week in cadence.weeks)


def test_the_average_is_per_week_of_the_period(user):
    for day in (2, 9, 16, 23):
        sent(user, on=dt.date(2026, 3, day))

    cadence = reports.build(user, march(), today=dt.date(2026, 3, 31)).cadence

    assert cadence.total == 4
    assert 0.5 < cadence.per_week < 1.5


def test_the_longest_gap_is_measured_inside_the_period(user):
    """A silence that began in February is February's, not March's."""
    sent(user, on=dt.date(2026, 2, 1))
    sent(user, on=dt.date(2026, 3, 20))

    cadence = reports.build(user, march(), today=dt.date(2026, 3, 31)).cadence

    # 1 March to 19 March is nineteen days of silence, not the seven weeks since February.
    assert cadence.longest_gap == 19


def test_a_period_still_running_is_only_a_gap_up_to_today(user):
    """Counting the rest of the month as silence would report a gap nobody has had yet."""
    sent(user, on=dt.date(2026, 3, 2))

    cadence = reports.build(user, march(), today=dt.date(2026, 3, 5)).cadence

    assert cadence.longest_gap == 3, "1 March, and 3 to 5 March"


def test_a_period_with_nothing_in_it_is_one_long_gap(user):
    cadence = reports.build(user, march(), today=dt.date(2026, 3, 31)).cadence

    assert cadence.total == 0
    assert cadence.longest_gap == 31


def test_the_run_is_weeks_in_a_row_ending_at_the_last_one(user):
    for day in (2, 9, 16, 23, 30):
        sent(user, on=dt.date(2026, 3, day))

    cadence = reports.build(user, march(), today=dt.date(2026, 3, 31)).cadence

    assert cadence.current_run >= 4


def test_a_quiet_last_week_ends_the_run(user):
    """Honest rather than kind: the run is zero, and the page shows a zero."""
    sent(user, on=dt.date(2026, 3, 2))

    cadence = reports.build(user, march(), today=dt.date(2026, 3, 31)).cadence

    assert cadence.current_run == 0


def test_the_chart_always_has_a_scale(user):
    """A `max` of zero is not a scale, and a browser draws nothing from one."""
    assert reports.build(user, march(), today=dt.date(2026, 3, 31)).cadence.busiest == 1


def test_a_week_reaching_outside_the_period_says_so(user):
    """A month rarely begins on the first day of a week, so the bars at each end usually
    cover fewer days than the rest. Marked rather than hidden: a short bar that is short
    because the week was short misleads. Nothing outside the period is counted in it.
    """
    sent(user, on=dt.date(2026, 2, 25))
    sent(user, on=dt.date(2026, 3, 1))

    weeks = reports.build(user, march(), today=dt.date(2026, 3, 31)).cadence.weeks

    assert weeks[0].partial, "the week straddling the first of the month"
    assert weeks[0].count == 1, "and February's application is not in it"
    assert not weeks[1].partial


def test_the_weeks_cover_the_whole_period(user):
    weeks = reports.build(user, march(), today=dt.date(2026, 3, 31)).cadence.weeks

    assert weeks[0].start <= dt.date(2026, 3, 1)
    assert weeks[-1].end >= dt.date(2026, 3, 31)


# ---------------------------------------------------- what came back, kept apart


def test_a_reply_in_the_period_to_an_older_application_counts_here(user):
    """Two questions, and this is the other one. Folding them together would produce a
    figure that is true of neither.
    """
    application = sent(user, on=dt.date(2026, 2, 10))
    change_status(
        application,
        Status.ACKNOWLEDGED,
        occurred_at=timezone.make_aware(dt.datetime(2026, 3, 5, 9, 0)),
    )

    report = reports.build(user, march(), today=dt.date(2026, 3, 31))

    assert report.total == 0, "nothing was sent in March"
    assert report.happened.replies == 1, "but a reply arrived in it"


def test_an_application_answered_three_times_had_one_reply(user):
    """First rather than any: counting each step would report a busier month than happened."""
    application = sent(user, on=dt.date(2026, 3, 2))
    for day, status in ((5, Status.ACKNOWLEDGED), (9, Status.SCREENING), (16, Status.INTERVIEWING)):
        change_status(
            application, status, occurred_at=timezone.make_aware(dt.datetime(2026, 3, day, 9, 0))
        )

    report = reports.build(user, march(), today=dt.date(2026, 3, 31))

    assert report.happened.replies == 1


def test_an_interview_attended_in_the_period_is_counted(user):
    application = sent(user, on=dt.date(2026, 3, 2))
    Interview.objects.create(
        owner=user,
        application=application,
        starts_at=timezone.make_aware(dt.datetime(2026, 3, 12, 10, 0)),
        ends_at=timezone.make_aware(dt.datetime(2026, 3, 12, 11, 0)),
        outcome=InterviewOutcome.DONE,
    )

    assert reports.build(user, march(), today=dt.date(2026, 3, 31)).happened.interviews == 1


def test_an_interview_still_ahead_was_not_attended(user):
    application = sent(user, on=dt.date(2026, 3, 2))
    Interview.objects.create(
        owner=user,
        application=application,
        starts_at=timezone.make_aware(dt.datetime(2026, 3, 25, 10, 0)),
        ends_at=timezone.make_aware(dt.datetime(2026, 3, 25, 11, 0)),
        outcome=InterviewOutcome.SCHEDULED,
    )

    assert reports.build(user, march(), today=dt.date(2026, 3, 31)).happened.interviews == 0


def test_offers_and_rejections_are_counted_when_they_happened(user):
    won = sent(user, on=dt.date(2026, 3, 2))
    lost = sent(user, on=dt.date(2026, 3, 3), company="Black Mesa")
    change_status(won, Status.OFFER, occurred_at=timezone.make_aware(dt.datetime(2026, 3, 20, 9)))
    change_status(
        lost, Status.REJECTED, occurred_at=timezone.make_aware(dt.datetime(2026, 4, 2, 9))
    )

    happened = reports.build(user, march(), today=dt.date(2026, 3, 31)).happened

    assert happened.offers == 1
    assert happened.rejections == 0, "April's rejection is April's"


# ---------------------------------------------------------------- where it went


def test_the_effort_is_tallied_by_source(user):
    sent(user, on=dt.date(2026, 3, 2), source="LinkedIn")
    sent(user, on=dt.date(2026, 3, 3), source="LinkedIn", company="Black Mesa")
    sent(user, on=dt.date(2026, 3, 4), source="", company="Umbrella")

    rows = {
        row.name: row.count
        for row in reports.build(user, march(), today=dt.date(2026, 3, 31)).sources
    }

    assert rows["LinkedIn"] == 2
    assert "Not recorded" in rows, "and what was not recorded is said, not dropped"


def test_a_company_in_three_fields_counts_in_all_three(user):
    from postulo.jobs.models import Industry

    application = sent(user, on=dt.date(2026, 3, 2))
    for name in ("Software", "Finance"):
        industry, _made = Industry.objects.get_or_create(owner=user, name=name)
        application.posting.company.industries.add(industry)

    names = [
        row.name for row in reports.build(user, march(), today=dt.date(2026, 3, 31)).industries
    ]

    assert set(names) == {"Software", "Finance"}


# ------------------------------------------------------------------- the page


def test_the_page_opens_on_the_month_in_progress(client, user):
    client.force_login(user)

    html = client.get(reverse(PAGE)).content.decode()

    assert timezone.localdate().strftime("%Y-%m") in html


def test_the_period_travels_in_the_address(client, user):
    """A question rather than a preference: a report for a month is a thing to bookmark and
    to send to somebody, which is the line this project already draws.
    """
    sent(user, on=dt.date(2026, 3, 4), role="Research Engineer")
    client.force_login(user)

    html = client.get(reverse(PAGE), {"period": "month", "on": "2026-03"}).content.decode()

    assert "Research Engineer" in html


def test_the_page_never_links_to_a_period_that_has_not_happened(client, user):
    client.force_login(user)

    page = client.get(reverse(PAGE)).context

    assert page["later"] == "", "there is no report about next month"
    assert page["earlier"], "and the one before is one click away"


def test_a_stranger_is_asked_to_sign_in(client):
    assert client.get(reverse(PAGE)).status_code == 302


def test_the_page_says_what_it_leaves_out(client, user):
    client.force_login(user)

    html = client.get(reverse(PAGE)).content.decode()

    assert "drafted but never sent" in html


def test_the_page_invents_no_target(client, user):
    """A benefit regime's requirement is that regime's, not this software's."""
    client.force_login(user)

    html = client.get(reverse(PAGE)).content.decode()

    assert "sets no target" in html


# --------------------------------------------------------------- out as a file


def test_the_csv_is_the_evidence_list(user):
    sent(user, on=dt.date(2026, 3, 4), role="Research Engineer", source="LinkedIn")

    text = reports.as_csv(reports.build(user, march(), today=dt.date(2026, 3, 31)))

    lines = text.strip().split("\n")
    assert len(lines) == 2, "a header and one row"
    assert "Research Engineer" in lines[1] and "LinkedIn" in lines[1]
    assert "2026-03-04" in lines[1]


def test_the_csv_downloads_with_the_period_in_its_name(client, user):
    client.force_login(user)

    response = client.get(reverse(CSV), {"period": "month", "on": "2026-03"})

    assert response["Content-Type"].startswith("text/csv")
    assert "2026-03-01" in response["Content-Disposition"]
    assert "attachment" in response["Content-Disposition"]


def test_the_csv_holds_nothing_from_outside_the_period(client, user):
    sent(user, on=dt.date(2026, 2, 27), role="February Role")
    sent(user, on=dt.date(2026, 3, 4), role="March Role")
    client.force_login(user)

    text = client.get(reverse(CSV), {"period": "month", "on": "2026-03"}).content.decode()

    assert "March Role" in text
    assert "February Role" not in text


def test_the_pdf_says_who_it_is_about_and_when_it_was_made(client, user, settings):
    """A document with no date is not evidence of anything."""
    from django.template.loader import render_to_string

    sent(user, on=dt.date(2026, 3, 4))
    report = reports.build(user, march(), today=dt.date(2026, 3, 31))

    html = render_to_string("applications/report_print.html", {"report": report})

    assert user.display_name in html
    assert str(report.produced_at.year) in html
    assert "Job search report" in html


def test_the_pdf_document_names_no_side_of_the_page():
    """A report written in Hebrew lays out from the other edge, and WeasyPrint honours it."""
    from pathlib import Path

    css = Path("src/postulo/templates/applications/report_print.html").read_text(encoding="utf-8")

    for physical in (
        "margin-left",
        "margin-right",
        "padding-left",
        "padding-right",
        "text-align: left",
    ):
        assert physical not in css


def test_the_pdf_route_says_so_when_no_renderer_is_installed(client, user, monkeypatch):
    """Tracking a search works perfectly with no renderer at all, so this is a sentence
    rather than a stack trace.
    """
    from postulo.documents import pdf as pdf_module

    def refuse(*args, **kwargs):
        raise pdf_module.PDFBackendUnavailable("no renderer here")

    monkeypatch.setattr("postulo.documents.pdf.html_to_pdf", refuse)
    client.force_login(user)

    response = client.get(reverse(PDF), follow=True)

    assert "no renderer here" in response.content.decode()


# ------------------------------------------------------- shared with the widgets


def test_what_counts_as_a_reply_is_read_rather_than_restated():
    """A widget and a report row are the same numbers at two lengths, so whichever came
    second reads the first's definitions.
    """
    from pathlib import Path

    from postulo.applications import analytics

    source = Path("src/postulo/applications/reports.py").read_text(encoding="utf-8")

    assert "from .analytics import RESPONSE_STATUSES" in source
    assert reports.RESPONSE_STATUSES is analytics.RESPONSE_STATUSES


def test_a_report_stores_nothing(user):
    """It is a snapshot of the record at a moment, which is what makes it evidence -- and
    the reason "edit this report" never has to mean anything.
    """
    sent(user, on=dt.date(2026, 3, 4))

    before = reports.build(user, march(), today=dt.date(2026, 3, 31))
    after = reports.build(user, march(), today=dt.date(2026, 3, 31))

    assert [row.pk for row in before.evidence] == [row.pk for row in after.evidence]
    assert not any(
        model._meta.model_name.startswith("report")
        for model in Application._meta.apps.get_app_config("applications").get_models()
    )


def test_a_hand_written_entry_counts_like_any_other(user):
    """The event log is the truth, and an interview typed in by hand leaves the same mark."""
    application = sent(user, on=dt.date(2026, 3, 2))
    record_event(
        application,
        kind=EventKind.NOTE,
        summary="Spoke to them on the phone",
        occurred_at=timezone.make_aware(dt.datetime(2026, 3, 10, 9, 0)),
    )

    report = reports.build(user, march(), today=dt.date(2026, 3, 31))

    assert report.evidence[0].last_activity_on == dt.date(2026, 3, 10)
