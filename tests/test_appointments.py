"""Appointments with an adviser at a public employment service, typed by hand (#888).

An :class:`Interview` is about an application; this is a meeting at the office the person is
registered with. It is a model of its own, a kind of its own on the calendar, in the diary
feed and in the report, in the archive, and it follows a company merge and a contact merge.
"""

from __future__ import annotations

import datetime as dt
import io
import json
import re
import zipfile

import pytest
from django.urls import reverse
from django.utils import timezone

from postulo.applications import agenda, ical, reports
from postulo.applications.forms import AppointmentForm
from postulo.applications.models import Appointment, AppointmentHow, Reminder
from postulo.applications.services import sync_appointment_reminder
from postulo.core import export as export_module
from postulo.core import importer
from postulo.jobs import merging
from postulo.jobs.models import Company, CompanyKind, Contact

pytestmark = pytest.mark.django_db

UTC = dt.UTC


@pytest.fixture
def office(user):
    return Company.objects.create(
        owner=user, name="France Travail", kind=CompanyKind.EMPLOYMENT_SERVICE
    )


@pytest.fixture
def adviser(user, office):
    return Contact.objects.create(owner=user, company=office, name="Mme Durand", email="d@ft.test")


def soon(days=5, hour=10) -> dt.datetime:
    day = timezone.now() + dt.timedelta(days=days)
    return day.replace(hour=hour, minute=0, second=0, microsecond=0)


@pytest.fixture
def appointment(user, office, adviser):
    return Appointment.objects.create(
        owner=user,
        company=office,
        contact=adviser,
        starts_at=soon(),
        ends_at=soon() + dt.timedelta(minutes=45),
        how=AppointmentHow.IN_PERSON,
        place="12 rue de la Paix",
        notes="Bring the CV",
    )


def posted(office, adviser=None, **more) -> dict:
    data = {
        "company": office.pk,
        "contact": adviser.pk if adviser else "",
        "starts_at": "2031-05-04T10:00",
        "ends_at": "",
        "how": "phone",
        "place": "01 23 45 67 89",
        "notes": "",
        "outcome_notes": "",
    }
    data.update(more)
    return data


# --------------------------------------------------------------------------- the form


def test_the_form_offers_only_the_employment_services_and_the_people_at_them(user, office, adviser):
    employer = Company.objects.create(owner=user, name="Black Mesa")
    Contact.objects.create(owner=user, company=employer, name="A Recruiter")

    form = AppointmentForm(user=user)

    assert list(form.fields["company"].queryset) == [office]
    assert list(form.fields["contact"].queryset) == [adviser]


def test_somebody_elses_service_is_not_offered_or_accepted(user, other_user, office):
    theirs = Company.objects.create(
        owner=other_user, name="Jobcentre", kind=CompanyKind.EMPLOYMENT_SERVICE
    )

    form = AppointmentForm(posted(theirs), user=user)

    assert not form.is_valid() and "company" in form.errors


def test_an_adviser_must_be_recorded_at_the_chosen_service(user, office):
    other = Company.objects.create(
        owner=user, name="Jobcentre Plus", kind=CompanyKind.EMPLOYMENT_SERVICE
    )
    elsewhere = Contact.objects.create(owner=user, company=other, name="Mr Smith")

    form = AppointmentForm(posted(office, elsewhere), user=user)

    assert not form.is_valid() and "contact" in form.errors


def test_it_cannot_end_before_it_starts(user, office):
    form = AppointmentForm(posted(office, ends_at="2031-05-04T09:00"), user=user)

    assert not form.is_valid() and "ends_at" in form.errors


# ------------------------------------------------------------------ the pages


def test_it_is_entered_from_the_calendar_with_its_day_already_in_it(client, user, office):
    client.force_login(user)

    page = client.get(reverse("applications:appointment_create"), {"on": "2031-05-04"})

    assert page.status_code == 200
    html = page.content.decode()
    # The date box and the time box the one value is drawn in (#933).
    assert re.search(r'name="starts_at" value="2031-05-04"', html)
    assert re.search(r'name="starts_at_time" value="10:00"', html)


def test_it_is_entered_from_the_services_own_page_and_returns_there(client, user, office):
    client.force_login(user)
    url = f"{reverse('applications:appointment_create')}?company={office.pk}"

    page = client.get(url)
    assert f'<option value="{office.pk}" selected' in page.content.decode()

    saved = client.post(url, posted(office))
    assert saved.status_code == 302
    assert saved["Location"] == reverse("jobs:company_detail", args=[office.pk])
    made = Appointment.objects.get()
    assert made.owner == user and made.company == office and made.how == "phone"


def test_a_new_appointment_asks_for_a_reminder_a_day_ahead_unless_told_not_to(client, user, office):
    client.force_login(user)
    soonish = (timezone.localtime() + dt.timedelta(days=9)).strftime("%Y-%m-%dT10:00")

    client.post(reverse("applications:appointment_create"), posted(office, starts_at=soonish))
    client.post(
        reverse("applications:appointment_create"),
        {**posted(office, starts_at=soonish), "remind": "on"},
    )

    first, second = Appointment.objects.order_by("pk")
    assert first.reminder_id is None, "an unticked box asks for none"
    assert second.reminder is not None and second.reminder.application_id is None
    assert second.reminder.due_at == second.starts_at - dt.timedelta(days=1)
    assert "France Travail" in second.reminder.summary


def test_moving_it_moves_its_reminder_and_unticking_takes_the_reminder_away(user, appointment):
    sync_appointment_reminder(appointment, remind=True)
    reminder = appointment.reminder
    Reminder.objects.filter(pk=reminder.pk).update(notified_at=timezone.now())

    appointment.starts_at += dt.timedelta(days=1)
    appointment.save()
    sync_appointment_reminder(appointment, remind=True)

    reminder.refresh_from_db()
    assert reminder.due_at == appointment.starts_at - dt.timedelta(days=1)
    assert reminder.notified_at is None, "moved into the future, so not yet announced"

    sync_appointment_reminder(appointment, remind=False)
    appointment.refresh_from_db()
    assert appointment.reminder_id is None
    assert not Reminder.objects.filter(pk=reminder.pk).exists()


def test_none_is_made_for_a_meeting_less_than_a_day_away(user, office):
    near = Appointment.objects.create(
        owner=user, company=office, starts_at=timezone.now() + dt.timedelta(hours=5)
    )

    sync_appointment_reminder(near, remind=True)

    assert near.reminder_id is None


def test_it_is_changed_and_deleted_with_its_reminder(client, user, appointment):
    client.force_login(user)
    sync_appointment_reminder(appointment, remind=True)
    reminder_id = appointment.reminder_id

    page = client.get(reverse("applications:appointment_update", args=[appointment.pk]))
    assert page.status_code == 200 and "12 rue de la Paix" in page.content.decode()

    ask = reverse("applications:appointment_delete", args=[appointment.pk])
    assert client.get(ask).status_code == 200
    assert Appointment.objects.count() == 1, "asking deletes nothing"
    done = client.post(ask)
    assert done.status_code == 302
    assert not Appointment.objects.exists()
    assert not Reminder.objects.filter(pk=reminder_id).exists()


def test_it_is_nobody_elses_to_see_or_change(client, other_user, appointment):
    client.force_login(other_user)

    for name in ("appointment_update", "appointment_delete"):
        url = reverse(f"applications:{name}", args=[appointment.pk])
        assert client.get(url).status_code == 404
        assert client.post(url).status_code == 404
    assert Appointment.objects.count() == 1


# ------------------------------------------------------------- the calendar


def test_it_is_a_kind_of_the_calendar_with_its_own_legend_entry(user, appointment):
    day = timezone.localdate(appointment.starts_at)

    page = agenda.page_for({"view": "day", "on": f"{day:%Y-%m-%d}"}, user)

    [event] = [event for event in page.events if event.kind == agenda.APPOINTMENT]
    assert event.title == "France Travail"
    assert "In person" in event.detail and "Mme Durand" in event.detail
    assert event.said == "Appointment:"
    assert event.tone == "cal-sky"
    assert agenda.APPOINTMENT in {kind for kind, *_rest in page.legend}


def test_the_kinds_parameter_narrows_to_it_and_away_from_it(user, appointment):
    day = f"{timezone.localdate(appointment.starts_at):%Y-%m-%d}"

    only = agenda.page_for({"view": "day", "on": day, "kinds": "appointment"}, user)
    without = agenda.page_for({"view": "day", "on": day, "kinds": "reminder"}, user)

    assert [event.kind for event in only.events] == ["appointment"]
    assert without.events == []


def test_an_attended_appointment_is_drawn_as_over_not_dropped(user, appointment):
    Appointment.objects.filter(pk=appointment.pk).update(attended=True)
    day = f"{timezone.localdate(appointment.starts_at):%Y-%m-%d}"

    page = agenda.page_for({"view": "day", "on": day}, user)

    [event] = page.events
    assert event.muted and event.said == "Appointment attended:"


def test_the_calendar_page_links_to_making_one(client, user, appointment):
    client.force_login(user)

    page = client.get(reverse("applications:calendar"))

    assert reverse("applications:appointment_create") in page.content.decode()
    assert "New appointment" in page.content.decode()


def test_somebody_elses_appointment_is_not_on_my_calendar(user, other_user, appointment):
    day = f"{timezone.localdate(appointment.starts_at):%Y-%m-%d}"

    page = agenda.page_for({"view": "day", "on": day}, other_user)

    assert page.events == []


# ------------------------------------------------------------------ the file


def test_the_pages_download_carries_it_as_a_meeting(client, user, appointment):
    client.force_login(user)
    day = f"{timezone.localdate(appointment.starts_at):%Y-%m-%d}"

    response = client.get(reverse("applications:calendar_download"), {"view": "day", "on": day})

    body = response.content.decode()
    assert response["Content-Type"].startswith("text/calendar")
    assert f"UID:{appointment.uid}" in body
    assert "SUMMARY:Appointment: France Travail" in body
    assert "LOCATION:12 rue de la Paix" in body
    assert "Bring the CV" not in body and "With: Mme Durand" in body
    assert "d@ft.test" not in body, "an address would make the adviser an invitee (#963)"
    assert "outcome" not in body.lower(), "what was agreed is the person's own"


def test_a_meeting_with_no_end_lasts_an_hour_in_the_file(user, office):
    start = soon()
    meeting = Appointment.objects.create(owner=user, company=office, starts_at=start)

    text = ical.calendar([], appointments=[meeting]).replace("\r\n", "\n")

    assert f"DTSTART:{start:%Y%m%dT%H%M%S}Z" in text
    assert f"DTEND:{start + dt.timedelta(hours=1):%Y%m%dT%H%M%S}Z" in text


def test_a_control_character_in_the_place_cannot_start_a_property(user, office):
    meeting = Appointment.objects.create(
        owner=user, company=office, starts_at=soon(), place="here\r\nATTENDEE:mailto:x@evil.test"
    )

    text = ical.calendar([], appointments=[meeting])

    assert "\r\nATTENDEE:mailto:x@evil.test" not in text


# ------------------------------------------------------------- the service's page


def test_the_services_page_lists_its_appointments_and_offers_another(
    client, user, office, appointment
):
    client.force_login(user)

    page = client.get(reverse("jobs:company_detail", args=[office.pk])).content.decode()

    assert 'id="appointments"' in page
    assert reverse("applications:appointment_update", args=[appointment.pk]) in page
    assert f"{reverse('applications:appointment_create')}?company={office.pk}" in page
    assert "12 rue de la Paix" in page


def test_an_ordinary_employers_page_has_no_such_section(client, user):
    employer = Company.objects.create(owner=user, name="Black Mesa")
    client.force_login(user)

    page = client.get(reverse("jobs:company_detail", args=[employer.pk])).content.decode()

    assert 'id="appointments"' not in page


# ------------------------------------------------------------------- the report


def test_the_report_counts_the_meetings_attended_in_the_period(user, office):
    inside = Appointment.objects.create(
        owner=user, company=office, starts_at=timezone.now() - dt.timedelta(days=3), attended=True
    )
    Appointment.objects.create(
        owner=user, company=office, starts_at=timezone.now() - dt.timedelta(days=2)
    )
    Appointment.objects.create(
        owner=user,
        company=office,
        starts_at=timezone.now() - dt.timedelta(days=400),
        attended=True,
    )
    today = timezone.localdate()
    period = reports.Period(start=today - dt.timedelta(days=30), end=today)

    report = reports.build(user, period)

    assert inside.attended and report.appointments_attended == 1


def test_the_report_page_says_it_when_there_is_an_office(client, user, office):
    client.force_login(user)

    page = client.get(reverse("applications:report")).content.decode()

    assert "Appointments attended" in page


# ----------------------------------------------------------------- the archive


def _round_trip(user, other_user):
    buffer = export_module.write_archive(user)
    archive = zipfile.ZipFile(buffer)
    document = json.loads(archive.read(export_module.MANIFEST_NAME))
    report = importer.load(other_user, archive)
    return document, report


def test_the_archive_carries_it_with_its_adviser_and_reminder(
    user, other_user, office, adviser, appointment
):
    sync_appointment_reminder(appointment, remind=True)
    Reminder.objects.create(owner=user, summary="Something else", due_at=soon(30))

    document, report = _round_trip(user, other_user)

    [written] = document["companies"][0]["appointments"]
    assert written["uid"] == appointment.uid and written["contact_id"] == adviser.pk
    assert written["reminder"]["summary"].startswith("Appointment tomorrow")
    assert [row["summary"] for row in document["reminders"]] == ["Something else"], (
        "the reminder made for an appointment travels with it, not among the loose ones"
    )
    assert document["counts"]["appointments"] == 1 == export_module.counts(user)["appointments"]
    assert report.appointments == 1

    [restored] = Appointment.objects.filter(owner=other_user)
    assert restored.company.name == "France Travail"
    assert restored.contact.name == "Mme Durand"
    assert (restored.place, restored.notes, restored.how) == (
        "12 rue de la Paix",
        "Bring the CV",
        "in_person",
    )
    assert restored.uid == appointment.uid
    assert restored.reminder is not None and restored.reminder.owner == other_user
    assert Reminder.objects.filter(owner=other_user).count() == 2


def test_importing_the_same_archive_again_does_not_duplicate_it(user, office, appointment):
    buffer = export_module.write_archive(user)

    importer.load(user, zipfile.ZipFile(buffer), force=True)

    assert Appointment.objects.filter(owner=user).count() == 1


def test_an_archive_from_before_the_format_has_none_and_a_bad_entry_is_refused_kindly(
    user, other_user, office
):
    document = json.loads(
        zipfile.ZipFile(export_module.write_archive(user)).read(export_module.MANIFEST_NAME)
    )
    company = document["companies"][0]
    company.pop("appointments")
    older = io.BytesIO()
    with zipfile.ZipFile(older, "w") as archive:
        archive.writestr(export_module.MANIFEST_NAME, json.dumps(document))
    older.seek(0)
    assert importer.load(other_user, zipfile.ZipFile(older)).appointments == 0

    company["appointments"] = [
        {"starts_at": None},
        "not an object",
        {"starts_at": "2031-05-04T10:00:00+00:00", "how": "carrier pigeon", "attended": "yes"},
        {
            "starts_at": "2031-05-05T10:00:00+00:00",
            "ends_at": "2031-05-05T09:00:00+00:00",
            "uid": "x" * 500,
        },
    ]
    odd = io.BytesIO()
    with zipfile.ZipFile(odd, "w") as archive:
        archive.writestr(export_module.MANIFEST_NAME, json.dumps(document))
    odd.seek(0)
    third = importer.load(other_user, zipfile.ZipFile(odd), force=True)

    assert third.appointments == 2
    assert any("no date" in line for line in third.skipped)
    kept = Appointment.objects.filter(owner=other_user).order_by("starts_at")
    assert [row.how for row in kept] == ["in_person", "in_person"], "an unknown way is the default"
    assert [row.attended for row in kept] == [False, False], "only a true is a yes"
    assert kept[1].ends_at is None, "an end before the start is dropped"


# ---------------------------------------------------------------------- merges


def test_merging_two_services_moves_the_meetings_and_merging_two_people_moves_the_adviser(
    user, office, adviser, appointment
):
    other_office = Company.objects.create(
        owner=user, name="France Travail (old)", kind=CompanyKind.EMPLOYMENT_SERVICE
    )
    old = Appointment.objects.create(owner=user, company=other_office, starts_at=soon(9))
    colleague = Contact.objects.create(owner=user, company=office, name="Mr Martin")
    Appointment.objects.filter(pk=appointment.pk).update(contact=colleague)

    plan = merging.plan_contacts(adviser, colleague)
    assert any("Appointments" in line.label for line in plan.moves)
    merging.merge_contacts(adviser, colleague)
    appointment.refresh_from_db()
    assert appointment.contact == adviser

    plan = merging.plan_companies(office, other_office)
    assert any("Appointments" in line.label for line in plan.moves)
    merging.merge_companies(office, other_office)
    old.refresh_from_db()
    assert old.company == office and Appointment.objects.count() == 2


def test_deleting_a_service_says_it_takes_its_appointments(client, user, office, appointment):
    client.force_login(user)

    page = client.get(reverse("jobs:company_delete", args=[office.pk])).content.decode()

    assert "1 appointment" in page


# ------------------------------------------------------------ the plugin surface


def test_a_plugin_adds_one_through_the_surface_and_cannot_aim_it_wrongly(user, office, adviser):
    from postulo.plugins import api

    made = api.add_appointment(
        office, starts_at=soon(6), how="video", place="https://meet.example/ft", contact=adviser
    )

    assert isinstance(made, api.Appointment) and made.owner == user
    assert made.how == api.AppointmentHow.VIDEO and made.is_link
    assert made.ends_at == made.starts_at + dt.timedelta(hours=1)
    assert made.reminder is not None, "a day ahead, as the form makes one"
    assert api.add_appointment(office, starts_at=soon(7), how="telepathy").how == "in_person"

    employer = Company.objects.create(owner=user, name="Black Mesa")
    with pytest.raises(ValueError):
        api.add_appointment(employer, starts_at=soon())
    stranger = Contact.objects.create(owner=user, company=employer, name="Someone")
    with pytest.raises(ValueError):
        api.add_appointment(office, starts_at=soon(), contact=stranger)
