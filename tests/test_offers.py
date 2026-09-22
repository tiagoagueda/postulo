"""Offers: what was offered, recorded and compared (#237).

The moment a search has the most at stake was the one Postulo recorded least: *offer* was
a status and a timeline kind, and the only money in the record was the advertised range.
What is held here: recording an offer is one row, one timeline entry, one reminder and a
status move, all through one service; a revision is written as one; the comparison brings
amounts to a year within a currency and decides nothing; and the record leaves with the
export and comes back.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from postulo.applications import agenda
from postulo.applications.forms import OfferForm
from postulo.applications.models import Application, EventKind, Offer, Reminder, Status
from postulo.applications.services import change_status, record_offer, revise_offer
from postulo.jobs.models import Company, JobPosting, SalaryPeriod

pytestmark = pytest.mark.django_db


@pytest.fixture
def application(user):
    company = Company.objects.create(owner=user, name="Aperture Science")
    posting = JobPosting.objects.create(
        owner=user, company=company, title="Test Engineer", salary_max=60000, salary_currency="EUR"
    )
    application = Application.objects.create(owner=user, posting=posting, status=Status.DRAFT)
    change_status(application, Status.INTERVIEWING)
    return application


def a_week_out() -> dt.date:
    return timezone.localdate() + dt.timedelta(days=7)


# ------------------------------------------------------------------- the model


def test_the_terms_read_as_one_line_and_a_year_is_arithmetic(user, application):
    offer = Offer(
        owner=user,
        application=application,
        base_amount=Decimal("5000"),
        currency="EUR",
        period=SalaryPeriod.MONTH,
    )
    assert offer.terms.endswith("EUR per month") and "5,000" in offer.terms
    assert offer.yearly_amount == Decimal("60000")

    hourly = Offer(
        owner=user,
        application=application,
        base_amount=Decimal("50"),
        currency="GBP",
        period=SalaryPeriod.HOUR,
    )
    assert hourly.yearly_amount == Decimal("84000"), "1,680 hours, the same year the sort uses"

    handed_an_int = Offer(owner=user, application=application, base_amount=65000, currency="EUR")
    assert handed_an_int.terms.startswith("65,000 EUR"), (
        "a row made a moment ago holds what it was given"
    )
    assert Offer(owner=user, application=application).terms == ""
    assert Offer(owner=user, application=application).yearly_amount is None


# --------------------------------------------------------------- recording one


def test_recording_an_offer_writes_the_timeline_moves_the_status_and_sets_a_reminder(
    user, application
):
    offer = record_offer(
        application,
        base_amount=Decimal("65000"),
        currency="EUR",
        period=SalaryPeriod.YEAR,
        answer_by=a_week_out(),
    )

    application.refresh_from_db()
    assert application.status == Status.OFFER
    kinds = list(application.events.values_list("kind", flat=True))
    assert EventKind.OFFER in kinds and EventKind.STATUS_CHANGE in kinds
    entry = application.events.get(kind=EventKind.OFFER)
    assert "65,000 EUR per year" in entry.summary

    assert offer.reminder is not None and not offer.reminder.is_done
    assert timezone.localdate(offer.reminder.due_at) == a_week_out() - dt.timedelta(days=1)
    assert "Aperture Science" in offer.reminder.summary


def test_an_offer_on_an_accepted_application_does_not_move_it_backwards(user, application):
    change_status(application, Status.ACCEPTED)

    record_offer(application, base_amount=Decimal("70000"), currency="EUR")

    application.refresh_from_db()
    assert application.status == Status.ACCEPTED


def test_no_answer_by_date_means_no_reminder(user, application):
    offer = record_offer(application, base_amount=Decimal("1"), currency="EUR")
    assert offer.reminder is None


def test_an_answer_date_already_passed_gets_no_reminder(user, application):
    offer = record_offer(application, answer_by=timezone.localdate() - dt.timedelta(days=1))
    assert offer.reminder is None


def test_revising_moves_the_reminder_and_says_so_on_the_timeline(user, application):
    offer = record_offer(
        application, base_amount=Decimal("65000"), currency="EUR", answer_by=a_week_out()
    )
    Reminder.objects.filter(pk=offer.reminder_id).update(notified_at=timezone.now())

    offer.base_amount = Decimal("70000")
    offer.answer_by = a_week_out() + dt.timedelta(days=7)
    offer.save()
    revise_offer(offer)

    reminder = Reminder.objects.get(pk=offer.reminder_id)
    assert timezone.localdate(reminder.due_at) == offer.answer_by - dt.timedelta(days=1)
    assert reminder.notified_at is None, "moved into the future is not yet announced"
    assert application.events.filter(
        kind=EventKind.OFFER, summary__startswith="Offer revised"
    ).exists()
    assert "70,000" in application.events.filter(kind=EventKind.OFFER).first().summary


def test_taking_the_answer_date_away_ticks_the_reminder_off(user, application):
    offer = record_offer(application, answer_by=a_week_out())
    offer.answer_by = None
    offer.save()
    revise_offer(offer)
    assert Reminder.objects.get(pk=offer.reminder_id).is_done


# ------------------------------------------------------------------- the form


def test_an_amount_needs_its_currency_and_the_code_is_upper_cased(user):
    form = OfferForm(user=user, data={"base_amount": "1000", "currency": "", "period": "year"})
    assert not form.is_valid() and "currency" in form.errors

    form = OfferForm(user=user, data={"base_amount": "1000", "currency": "eur", "period": "year"})
    assert form.is_valid(), form.errors
    assert form.cleaned_data["currency"] == "EUR"

    form = OfferForm(user=user, data={"base_amount": "1000", "currency": "12A", "period": "year"})
    assert not form.is_valid()


# ------------------------------------------------------------------ the pages


def test_recording_from_the_page_starts_from_the_advertised_range(client, user, application):
    client.force_login(user)
    html = client.get(reverse("applications:offer_create", args=[application.pk])).content.decode()
    assert 'value="60000' in html and 'value="EUR"' in html

    response = client.post(
        reverse("applications:offer_create", args=[application.pk]),
        {
            "base_amount": "65000",
            "currency": "EUR",
            "period": "year",
            "answer_by": a_week_out().isoformat(),
        },
    )
    assert response.status_code == 302
    assert Offer.objects.filter(application=application).count() == 1


def test_the_application_page_lists_its_offers_and_links_the_comparison(client, user, application):
    record_offer(application, base_amount=Decimal("65000"), currency="EUR")
    client.force_login(user)
    html = client.get(application.get_absolute_url()).content.decode()
    assert 'id="offers"' in html and "65,000 EUR per year" in html
    assert reverse("applications:offer_compare") in html


def test_editing_and_deleting_from_the_page(client, user, application):
    offer = record_offer(application, base_amount=Decimal("65000"), currency="EUR")
    client.force_login(user)

    response = client.post(
        reverse("applications:offer_update", args=[offer.pk]),
        {"base_amount": "66000", "currency": "EUR", "period": "year"},
    )
    assert response.status_code == 302
    offer.refresh_from_db()
    assert offer.base_amount == Decimal("66000")

    response = client.post(reverse("applications:offer_delete", args=[offer.pk]))
    assert response.status_code == 302
    assert not Offer.objects.filter(pk=offer.pk).exists()


def test_the_comparison_is_the_latest_offer_of_each_application_at_offer(client, user, application):
    record_offer(application, base_amount=Decimal("60000"), currency="EUR")
    record_offer(
        application, base_amount=Decimal("5500"), currency="EUR", period=SalaryPeriod.MONTH
    )
    other = Application.objects.create(
        owner=user,
        posting=JobPosting.objects.create(
            owner=user,
            company=Company.objects.create(owner=user, name="Black Mesa"),
            title="Physicist",
        ),
        status=Status.DRAFT,
    )
    record_offer(other, base_amount=Decimal("70000"), currency="USD")
    change_status(other, Status.REJECTED)
    client.force_login(user)

    response = client.get(reverse("applications:offer_compare"))
    html = response.content.decode()

    assert [o.application_id for o in response.context["columns"]] == [application.pk], (
        "one column, the latest"
    )
    rows = dict(response.context["rows"])
    assert rows["Base pay"] == [("Aperture Science", "5,500 EUR per month")]
    assert rows["Brought to a year"] == [("Aperture Science", "66,000 EUR a year")]
    assert "Black Mesa" not in html, "no longer standing at Offer"
    assert 'data-label="Aperture Science"' in html and "table-cards" in html


def test_nobody_elses_offer_is_reachable(client, user, other_user, application):
    offer = record_offer(application, base_amount=Decimal("1"), currency="EUR")
    client.force_login(other_user)
    assert client.get(reverse("applications:offer_update", args=[offer.pk])).status_code == 404
    assert client.get(reverse("applications:offer_compare")).context["columns"] == []


# ---------------------------------------------------------------- the calendar


def test_the_answer_by_date_is_on_the_calendar_until_it_is_answered(user, application):
    offer = record_offer(application, answer_by=a_week_out())
    today = timezone.localdate()

    (event,) = agenda.events_between(
        user, today, today + dt.timedelta(days=10), kinds=[agenda.ANSWER]
    )
    assert event.all_day and not event.muted
    assert "Aperture Science" in event.title and event.url == offer.get_absolute_url()

    change_status(application, Status.ACCEPTED)
    (answered,) = agenda.events_between(
        user, today, today + dt.timedelta(days=10), kinds=[agenda.ANSWER]
    )
    assert answered.muted


# ------------------------------------------------------------------- the API


def token_for(user, scopes="read"):
    from postulo.api.models import ApiToken

    _token, raw = ApiToken.issue(owner=user, name="test", scopes=scopes.split())
    return {"HTTP_AUTHORIZATION": f"Bearer {raw}"}


def test_the_api_reads_offers(client, user, application):
    offer = record_offer(
        application, base_amount=Decimal("5000"), currency="EUR", period=SalaryPeriod.MONTH
    )
    headers = token_for(user)

    listed = client.get("/api/v1/offers", **headers).json()
    assert [row["id"] for row in listed["items"]] == [offer.pk]
    assert Decimal(listed["items"][0]["yearly_amount"]) == 60000

    one = client.get(f"/api/v1/offers/{offer.pk}", **headers).json()
    assert one["currency"] == "EUR" and one["application_id"] == application.pk

    detail = client.get(f"/api/v1/applications/{application.pk}", **headers).json()
    assert [row["id"] for row in detail["offers"]] == [offer.pk]


# ------------------------------------------------------------- export and import


def test_offers_leave_with_the_export_and_come_back(user, other_user, application):
    import zipfile

    from postulo.core import export as export_module
    from postulo.core import importer

    record_offer(
        application,
        base_amount=Decimal("65000"),
        currency="EUR",
        answer_by=a_week_out(),
        benefits="Pension",
    )

    importer.load(other_user, zipfile.ZipFile(export_module.write_archive(user)))

    restored = Offer.objects.get(owner=other_user)
    assert restored.base_amount == Decimal("65000") and restored.benefits == "Pension"
    assert restored.answer_by == a_week_out()
    assert restored.reminder is not None and restored.reminder.owner == other_user
