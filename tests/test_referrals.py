"""Who referred you, and through which agency (#239).

*Referral* and *Recruiter* have been channels since the beginning and nothing recorded who.
An application can now name the contact who put the person forward and the agency it went
through, and the figures count what each led to.

**The posting's company stays the employer.** An agency is deliberately not a kind of
company, and naming one on an application changes nothing about who the application was
to. **Both are the owner's own records and nobody else's**: a form offers only theirs, and
the API refuses an id that is not -- with the one answer it gives for an id that does not
exist, so the refusal confirms nothing about what another account holds.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import zipfile

import pytest
from django.urls import reverse
from django.utils import timezone

from postulo.api.models import ApiToken
from postulo.applications import analytics, reports
from postulo.applications.forms import ApplicationForm
from postulo.applications.models import Application, Status
from postulo.applications.services import change_status
from postulo.core import export, gdpr, importer
from postulo.jobs.models import Company, Contact, JobPosting

pytestmark = pytest.mark.django_db


@pytest.fixture
def employer(user):
    return Company.objects.create(owner=user, name="Aperture Science")


@pytest.fixture
def agency(user):
    return Company.objects.create(owner=user, name="Hays")


@pytest.fixture
def friend(user):
    """Somebody at no company at all, which is who refers people as often as not."""
    return Contact.objects.create(owner=user, name="Chell", email="chell@example.org")


@pytest.fixture
def application(user, employer):
    posting = JobPosting.objects.create(owner=user, company=employer, title="Test Engineer")
    application = Application.objects.create(owner=user, posting=posting, status=Status.DRAFT)
    change_status(application, Status.APPLIED, occurred_at=timezone.now() - dt.timedelta(days=9))
    return application


def theirs(other_user):
    """A contact and a company belonging to somebody else."""
    company = Company.objects.create(owner=other_user, name="Black Mesa")
    contact = Contact.objects.create(owner=other_user, company=company, name="Gordon Freeman")
    return contact, company


def bearer(user, *scopes) -> dict:
    _record, raw = ApiToken.issue(user, "Agent", scopes=scopes or ("read", "write"))
    return {"HTTP_AUTHORIZATION": f"Bearer {raw}"}


def post_json(client, path, payload, **headers):
    return client.post(path, data=json.dumps(payload), content_type="application/json", **headers)


def posted(application, **changes) -> dict:
    """What the edit form posts for ``application``, with ``changes`` made."""
    data = {
        "status": application.status,
        "channel": application.channel,
        "priority": application.priority,
        "new_tags": "",
    }
    data.update({name: value for name, value in changes.items() if value is not None})
    return data


# --------------------------------------------------------------------- the record


def test_both_are_optional_and_neither_is_the_employer(application, friend, agency):
    assert application.referred_by is None and application.through_agency is None

    application.referred_by, application.through_agency = friend, agency
    application.save()
    application.refresh_from_db()

    assert application.referred_by == friend
    assert application.through_agency == agency
    assert application.company.name == "Aperture Science", "the posting's company is the employer"
    assert list(friend.referrals.all()) == [application]
    assert list(agency.placements.all()) == [application]


def test_the_application_outlives_the_referrer_and_the_agency(application, friend, agency):
    application.referred_by, application.through_agency = friend, agency
    application.save()

    friend.delete()
    agency.delete()
    application.refresh_from_db()

    assert application.referred_by is None and application.through_agency is None
    assert application.events.exists(), "and its timeline is where it was"


# ----------------------------------------------------------------------- the form


def test_the_form_offers_only_the_owners_own(user, other_user, application, friend, agency):
    their_contact, their_company = theirs(other_user)

    form = ApplicationForm(instance=application, user=user)

    assert list(form.fields["referred_by"].queryset) == [friend]
    assert their_contact not in form.fields["referred_by"].queryset
    assert list(form.fields["through_agency"].queryset) == [agency]
    assert their_company not in form.fields["through_agency"].queryset


def test_a_referrer_may_be_anywhere_and_the_main_contact_may_not(user, application, friend):
    """The main contact is somebody at this company. A referrer is whoever it was."""
    elsewhere = Company.objects.create(owner=user, name="Weyland-Yutani")
    colleague = Contact.objects.create(owner=user, company=elsewhere, name="Ellen Ripley")
    insider = Contact.objects.create(
        owner=user, company=application.posting.company, name="Cave Johnson"
    )

    form = ApplicationForm(instance=application, user=user)

    assert set(form.fields["referred_by"].queryset) == {friend, colleague, insider}
    assert set(form.fields["contact"].queryset) == {insider}


def test_two_people_of_one_name_are_told_apart_in_the_list(user, application):
    here = Contact.objects.create(
        owner=user, company=application.posting.company, name="Sam Porter"
    )
    nowhere = Contact.objects.create(owner=user, name="Sam Porter")

    field = ApplicationForm(instance=application, user=user).fields["referred_by"]

    assert field.label_from_instance(here) == "Sam Porter · Aperture Science"
    assert field.label_from_instance(nowhere) == "Sam Porter"


def test_the_employer_is_not_offered_as_its_own_agency(user, application, agency):
    form = ApplicationForm(instance=application, user=user)

    assert application.posting.company not in form.fields["through_agency"].queryset
    assert agency in form.fields["through_agency"].queryset


def test_neither_is_offered_where_there_is_nobody_to_choose(user, application):
    """A picker of one empty option is a control asking to be ignored."""
    form = ApplicationForm(instance=application, user=user)

    assert "referred_by" not in form.fields
    assert "through_agency" not in form.fields


def test_the_edit_page_records_both(client, user, application, friend, agency):
    client.force_login(user)

    response = client.post(
        reverse("applications:update", args=[application.pk]),
        posted(application, referred_by=friend.pk, through_agency=agency.pk),
    )

    assert response.status_code == 302, response.context["form"].errors
    application.refresh_from_db()
    assert application.referred_by == friend and application.through_agency == agency
    assert application.status == Status.APPLIED, "and nothing else moved"


def test_somebody_elses_contact_is_not_a_valid_choice(
    client, user, other_user, application, friend, agency
):
    their_contact, their_company = theirs(other_user)
    client.force_login(user)

    response = client.post(
        reverse("applications:update", args=[application.pk]),
        posted(application, referred_by=their_contact.pk, through_agency=their_company.pk),
    )

    assert response.status_code == 200, "the form comes back; nothing was saved"
    errors = response.context["form"].errors
    assert "referred_by" in errors and "through_agency" in errors
    application.refresh_from_db()
    assert application.referred_by is None and application.through_agency is None


def test_a_form_without_the_two_fields_leaves_what_the_row_says(user, application, friend):
    """Not offered is not cleared: a field a form does not have is a column it does not write."""
    Application.objects.filter(pk=application.pk).update(referred_by=friend)
    application.refresh_from_db()
    form = ApplicationForm(posted(application), instance=application, user=user)
    del form.fields["referred_by"]

    assert form.is_valid(), form.errors
    form.save()

    application.refresh_from_db()
    assert application.referred_by == friend


def test_the_contact_an_application_names_stays_on_its_list_wherever_they_are(user, application):
    """Somebody who moved company, or was merged into a record of themselves at another,
    is not at the employer any more. Left off the list, the next save of the form for any
    other reason would clear them without a word."""
    elsewhere = Company.objects.create(owner=user, name="Weyland-Yutani")
    moved = Contact.objects.create(owner=user, company=elsewhere, name="Ellen Ripley")
    Application.objects.filter(pk=application.pk).update(contact=moved)
    application.refresh_from_db()

    form = ApplicationForm(
        posted(application, contact=moved.pk, referred_by=""), instance=application, user=user
    )

    assert moved in form.fields["contact"].queryset
    assert form.is_valid(), form.errors
    form.save()
    application.refresh_from_db()
    assert application.contact == moved


def test_an_agency_that_is_the_employer_already_stays_on_its_list(user, application, agency):
    """Two companies merged into one can leave an application through its own employer."""
    employer = application.posting.company
    Application.objects.filter(pk=application.pk).update(through_agency=employer)
    application.refresh_from_db()

    form = ApplicationForm(instance=application, user=user)

    assert employer in form.fields["through_agency"].queryset


def test_the_page_says_who_referred_and_through_whom(client, user, application, friend, agency):
    Application.objects.filter(pk=application.pk).update(referred_by=friend, through_agency=agency)
    client.force_login(user)

    page = client.get(application.get_absolute_url()).content.decode()

    assert "data-referred-by" in page and "Chell" in page
    assert "data-through-agency" in page and "Hays" in page
    assert agency.get_absolute_url() in page


def test_the_page_says_nothing_where_there_is_nothing_to_say(client, user, application):
    client.force_login(user)

    page = client.get(application.get_absolute_url()).content.decode()

    assert "data-referred-by" not in page and "data-through-agency" not in page


# ------------------------------------------------------------------------ the API


def test_the_api_records_both_with_the_application(client, user, friend, agency):
    response = post_json(
        client,
        "/api/v1/applications",
        {
            "company_name": "Aperture Science",
            "title": "Test Engineer",
            "referred_by_id": friend.pk,
            "through_agency_id": agency.pk,
        },
        **bearer(user),
    )

    assert response.status_code == 201, response.content
    body = response.json()
    assert body["referred_by_id"] == friend.pk
    assert body["through_agency_id"] == agency.pk
    assert body["listing"]["company"]["name"] == "Aperture Science", "still the employer"
    made = Application.objects.get(pk=body["id"])
    assert made.referred_by == friend and made.through_agency == agency


def test_applying_to_a_listing_takes_them_too(client, user, employer, friend, agency):
    listing = JobPosting.objects.create(owner=user, company=employer, title="Test Engineer")

    response = post_json(
        client,
        f"/api/v1/listings/{listing.pk}/apply",
        {"referred_by_id": friend.pk, "through_agency_id": agency.pk},
        **bearer(user),
    )

    assert response.status_code == 201, response.content
    made = Application.objects.get(posting=listing)
    assert made.referred_by == friend and made.through_agency == agency


@pytest.mark.parametrize("field", ["referred_by_id", "through_agency_id"])
def test_the_api_refuses_somebody_elses(client, user, other_user, field):
    their_contact, their_company = theirs(other_user)
    wanted = their_contact.pk if field == "referred_by_id" else their_company.pk

    response = post_json(
        client,
        "/api/v1/applications",
        {"company_name": "Aperture Science", "title": "Test Engineer", field: wanted},
        **bearer(user),
    )

    assert response.status_code == 422
    assert field in response.content.decode()
    assert not Application.objects.for_user(user).exists()
    assert not Company.objects.for_user(user).exists(), "refused before anything was made"


@pytest.mark.parametrize("field", ["referred_by_id", "through_agency_id"])
def test_the_refusal_says_the_same_for_one_that_does_not_exist(client, user, other_user, field):
    """Otherwise the difference between the two answers would say which ids are somebody's."""
    their_contact, their_company = theirs(other_user)
    wanted = their_contact.pk if field == "referred_by_id" else their_company.pk
    payload = {"company_name": "Aperture Science", "title": "Test Engineer"}
    headers = bearer(user)

    somebodys = post_json(client, "/api/v1/applications", {**payload, field: wanted}, **headers)
    nobodys = post_json(client, "/api/v1/applications", {**payload, field: 987654}, **headers)

    assert somebodys.status_code == nobodys.status_code == 422
    assert somebodys.json()["detail"] == nobodys.json()["detail"]


def test_applying_refuses_somebody_elses_as_well(client, user, other_user, employer):
    their_contact, _their_company = theirs(other_user)
    listing = JobPosting.objects.create(owner=user, company=employer, title="Test Engineer")

    response = post_json(
        client,
        f"/api/v1/listings/{listing.pk}/apply",
        {"referred_by_id": their_contact.pk},
        **bearer(user),
    )

    assert response.status_code == 422
    assert not Application.objects.filter(posting=listing).exists()


def test_the_api_says_who_in_lists_and_alone(client, user, application, friend, agency):
    Application.objects.filter(pk=application.pk).update(referred_by=friend, through_agency=agency)
    headers = bearer(user, "read")

    alone = client.get(f"/api/v1/applications/{application.pk}", **headers).json()
    [listed] = client.get("/api/v1/applications", **headers).json()["items"]

    for body in (alone, listed):
        assert body["referred_by_id"] == friend.pk
        assert body["through_agency_id"] == agency.pk


def test_the_schema_describes_both(client, user):
    schema = client.get("/api/v1/openapi.json", **bearer(user, "read")).json()
    shapes = schema["components"]["schemas"]

    for name in ("ApplicationOut", "ApplicationDetailsIn", "ApplicationIn"):
        assert {"referred_by_id", "through_agency_id"} <= set(shapes[name]["properties"]), name


# -------------------------------------------------------------------- the figures


def sent(
    user, employer, title, *, referrer=None, agency=None, then=(), ago=dt.timedelta(days=5)
) -> Application:
    """An application sent `ago`. The monthly report reads the month it was sent in, so a
    test of this month's report sends it now: five days back is last month for the first
    five days of every month, which is how two of these failed on the 1st of October."""
    posting = JobPosting.objects.create(owner=user, company=employer, title=title)
    application = Application.objects.create(
        owner=user,
        posting=posting,
        status=Status.DRAFT,
        referred_by=referrer,
        through_agency=agency,
    )
    change_status(application, Status.APPLIED, occurred_at=timezone.now() - ago)
    for status in then:
        change_status(application, status)
    return application


def test_the_figures_count_what_each_referrer_and_agency_led_to(user, employer, friend, agency):
    other_agency = Company.objects.create(owner=user, name="Michael Page")
    sent(user, employer, "A", referrer=friend, then=(Status.INTERVIEWING, Status.OFFER))
    sent(user, employer, "B", referrer=friend)
    sent(user, employer, "C", agency=agency, then=(Status.SCREENING,))
    sent(user, employer, "D", agency=agency)
    sent(user, employer, "E", agency=other_agency)
    sent(user, employer, "F")

    insights = analytics.build(user)

    [referrer] = insights.by_referrer
    assert (referrer.name, referrer.applied, referrer.responded) == ("Chell", 2, 1)
    assert (referrer.interviewed, referrer.offers) == (1, 1)
    agencies = {row.name: (row.applied, row.responded) for row in insights.by_agency}
    assert agencies == {"Hays": (2, 1), "Michael Page": (1, 0)}
    assert [row.name for row in insights.by_agency] == ["Hays", "Michael Page"], "busiest first"
    assert sum(row.applied for row in insights.sources) == 6, "and the sources are unmoved"


def test_two_referrers_of_one_name_are_two_lines(user, employer, friend):
    namesake = Contact.objects.create(owner=user, company=employer, name="Chell")
    sent(user, employer, "A", referrer=friend)
    sent(user, employer, "B", referrer=namesake)

    names = sorted(row.name for row in analytics.build(user).by_referrer)

    assert names == ["Chell", "Chell · Aperture Science"]


def test_an_application_never_sent_is_in_neither(user, employer, friend, agency):
    posting = JobPosting.objects.create(owner=user, company=employer, title="Draft")
    Application.objects.create(
        owner=user, posting=posting, status=Status.DRAFT, referred_by=friend, through_agency=agency
    )

    insights = analytics.build(user)

    assert insights.by_referrer == [] and insights.by_agency == []


def test_the_figures_never_name_somebody_elses(user, other_user, employer, friend):
    sent(user, employer, "A", referrer=friend)

    assert analytics.build(other_user).by_referrer == []


def test_renaming_a_referrer_renames_them_in_the_figures(user, employer, friend):
    """The figures are kept until something they read changes, and a name is something they
    read."""
    sent(user, employer, "A", referrer=friend)
    before = analytics.fingerprint(user)

    friend.name = "Chell Johnson"
    friend.save()

    assert analytics.fingerprint(user) != before
    assert analytics.insights_for(user).by_referrer[0].name == "Chell Johnson"


def show(user, *keys):
    profile = user.profile
    profile.dashboard_widgets = list(keys)
    profile.save(update_fields=["dashboard_widgets"])


def test_the_sources_widget_gains_the_two_tables(client, user, employer, friend, agency):
    sent(user, employer, "A", referrer=friend, agency=agency)
    show(user, "sources")
    client.force_login(user)

    page = client.get(reverse("core:home")).content.decode()
    widget = page[page.index('data-widget="sources"') :]

    assert "By referrer" in widget and "data-by-referrer" in widget
    assert "By agency" in widget and "data-by-agency" in widget
    referrers = widget[widget.index("data-by-referrer") : widget.index("data-by-agency")]
    assert "Chell" in referrers and '<table class="table">' in referrers
    assert "Hays" in widget[widget.index("data-by-agency") :]


def test_the_widget_draws_neither_where_nothing_names_one(client, user, employer):
    sent(user, employer, "A")
    show(user, "sources")
    client.force_login(user)

    page = client.get(reverse("core:home")).content.decode()

    assert "data-by-referrer" not in page and "data-by-agency" not in page


def test_the_api_gives_the_two_breakdowns(client, user, employer, friend, agency):
    sent(user, employer, "A", referrer=friend, agency=agency)

    body = client.get("/api/v1/insights", **bearer(user, "read")).json()

    assert [row["name"] for row in body["by_referrer"]] == ["Chell"]
    assert [row["name"] for row in body["by_agency"]] == ["Hays"]
    assert body["endings"]["total"] == 0


# ------------------------------------------------------------- out, and back in again


def test_both_travel_in_the_archive_and_come_back(user, other_user, application, friend, agency):
    Application.objects.filter(pk=application.pk).update(referred_by=friend, through_agency=agency)

    document = export.build_document(user)

    # 20 is the format that added them (#239); the number itself is pinned in
    # test_phone_verification.py, where a change to it is written down.
    assert document["postulo"]["format"] == export.FORMAT_VERSION >= 20
    employer = next(c for c in document["companies"] if c["name"] == "Aperture Science")
    exported = employer["postings"][0]["applications"][0]
    assert exported["referred_by_id"] == friend.pk
    assert exported["through_agency"] == "Hays"
    assert [row["name"] for row in document["contacts"]] == ["Chell"], "at no company, and carried"

    importer.load(other_user, zipfile.ZipFile(export.write_archive(user)))

    restored = Application.objects.get(owner=other_user)
    assert restored.referred_by.name == "Chell"
    assert restored.referred_by.owner == other_user and restored.referred_by.company is None
    assert restored.referred_by.email == "chell@example.org"
    assert restored.through_agency.name == "Hays"
    assert restored.through_agency.owner == other_user
    assert restored.posting.company.name == "Aperture Science"


def test_a_referrer_at_a_company_later_in_the_file_is_still_found(user, other_user, application):
    """The application is read before the company its referrer works at, which is why both
    are resolved once everything in the file exists."""
    later = Company.objects.create(owner=user, name="Zeta Works")
    colleague = Contact.objects.create(owner=user, company=later, name="Doug Rattmann")
    Application.objects.filter(pk=application.pk).update(
        referred_by=colleague, through_agency=later
    )
    names = [company["name"] for company in export.build_document(user)["companies"]]
    assert names.index("Aperture Science") < names.index("Zeta Works")

    importer.load(other_user, zipfile.ZipFile(export.write_archive(user)))

    restored = Application.objects.get(owner=other_user)
    assert restored.referred_by.name == "Doug Rattmann"
    assert restored.referred_by.company.name == "Zeta Works"
    assert restored.through_agency.name == "Zeta Works"


def test_the_people_at_no_company_are_in_the_archive_with_how_to_reach_them(user, friend):
    from postulo.core.models import WebLink

    WebLink.objects.create(
        owner=user, holder=friend, kind=WebLink.Kind.WEBSITE, url="https://chell.example.org"
    )

    [exported] = export.build_document(user)["contacts"]

    assert exported["name"] == "Chell" and exported["department"] == ""
    assert [row["url"] for row in exported["web_links"]] == ["https://chell.example.org"]


def test_the_spreadsheet_gains_four_columns_after_the_seven(user, employer, friend, agency):
    this_month = dt.timedelta(0)
    ended = sent(
        user,
        employer,
        "Ended",
        referrer=friend,
        agency=agency,
        then=("interviewing",),
        ago=this_month,
    )
    change_status(ended, Status.REJECTED, end_reason="pay")
    sent(user, employer, "Live", ago=this_month)
    today = timezone.localdate()

    text = reports.as_csv(reports.build(user, reports.month_period(today.year, today.month)))
    header, *rows = list(csv.reader(io.StringIO(text)))

    assert header[:7] == [
        "Applied on",
        "Company",
        "Role",
        "Found via",
        "Address",
        "Status",
        "Last activity",
    ], "where a sheet that reads them by position expects them"
    assert header[7:] == ["Through agency", "Referred by", "Last stage reached", "Why it ended"]
    by_role = {row[2]: row for row in rows}
    assert by_role["Ended"][7:] == ["Hays", "Chell", "Interviewing", "The pay"]
    assert by_role["Live"][7:] == ["", "", "", ""]


def test_the_report_page_does_not_hand_a_referrers_name_to_an_office(
    client, user, employer, friend
):
    """The page and the document are what an employment office is given. Who referred
    somebody is another person's name and the applicant's own business."""
    sent(user, employer, "Role", referrer=friend, ago=dt.timedelta(0))
    client.force_login(user)

    page = client.get(reverse("applications:report")).content.decode()

    assert "Role" in page and "Chell" not in page


# --------------------------------------------------------------------- an erasure


def test_erasing_a_referrer_says_the_applications_lost_them(user, application, friend):
    Application.objects.filter(pk=application.pk).update(referred_by=friend)

    report = gdpr.erase_contact(friend)

    application.refresh_from_db()
    assert application.referred_by is None
    # `listing_events` since #270: the entries in listings' histories that came from them.
    assert report.unlinked == {
        "applications": 0,
        "referrals": 1,
        "interviews": 0,
        "listing_events": 0,
    }
    assert "1 application kept, without its referrer." in report.summary()
    assert "main contact" not in report.summary()


def test_the_dry_run_counts_an_application_a_referrer_would_leave(user, application, friend):
    from postulo.core.models import SiteSettings

    settings = SiteSettings.get()
    settings.retention_days = 30
    settings.save()
    Application.objects.filter(pk=application.pk).update(referred_by=friend)
    Contact.objects.filter(pk=friend.pk).update(created_at=timezone.now() - dt.timedelta(days=45))

    row = gdpr.retention_dry_run()

    assert row["would_remove"]["applications_unlinked"] == 1
