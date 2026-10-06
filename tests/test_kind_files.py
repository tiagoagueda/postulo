"""One kind of record as a file of its own: out of one account and into another (#659).

Each of the four kinds is written from the archive's builders, read back through a review,
and added only when somebody says so. The things held here: a file round-trips between two
accounts with nothing of the first in the second; a second import adds nothing; a block that
changes shape is a new version of its file; and what is not the file's to say writes nothing.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
import zipfile

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from postulo.applications.models import Application, ApplicationEvent, Interview, Status
from postulo.applications.services import (
    create_application,
    get_or_create_company,
    schedule_interview,
)
from postulo.core import export, importer, kind_files
from postulo.core.models import PhoneNumber, WebLink
from postulo.jobs.models import Company, Contact, Department, Industry, JobPosting
from postulo.jobs.services import set_identifiers
from postulo.resume import candidate

pytestmark = pytest.mark.django_db

KINDS = tuple(kind_files.FORMATS)


def furnish(owner) -> None:
    """One of everything the four files carry, in ``owner``'s account."""
    parent = get_or_create_company(owner, "Weyland Holdings")
    aperture = Company.objects.create(
        owner=owner,
        name="Aperture Science",
        website="https://aperture.example",
        location="Cambridge",
        parent=parent,
        notes="Cave Johnson's lab.",
    )
    aperture.industries.add(*Industry.named(owner, ["Research"]))
    Department.objects.create(owner=owner, company=aperture, name="Testing")
    set_identifiers(aperture, [("wikidata", "Q95", "")])

    contact = Contact.objects.create(
        owner=owner,
        company=aperture,
        name="Glados",
        role="Recruiter",
        email="glados@aperture.example",
        notes="Cake.",
    )
    PhoneNumber.objects.create(
        owner=owner, holder=contact, number="+351912345678", kind="mobile", is_primary=True
    )
    WebLink.objects.create(
        owner=owner, holder=contact, kind="website", url="https://glados.example", is_primary=True
    )
    Contact.objects.create(owner=owner, name="A friend")

    application = create_application(
        owner,
        company=aperture,
        posting_data={
            "title": "Test subject",
            "url": "https://aperture.example/jobs/1",
            "location": "Cambridge",
            "salary_min": 40000,
            "salary_max": 50000,
            "salary_currency": "EUR",
            "salary_period": "year",
            "description": "Run the tests.",
        },
        application_data={
            "status": Status.APPLIED,
            "applied_at": timezone.now() - dt.timedelta(days=10),
        },
    )
    schedule_interview(
        application,
        kind="video",
        starts_at=timezone.now() + dt.timedelta(days=3),
        location="https://meet.example/aperture",
    )
    JobPosting.objects.create(
        owner=owner, company=aperture, title="Janitor", url="https://aperture.example/jobs/2"
    )


def document_of(user, kind: str) -> dict:
    return kind_files.build_document(user, kind)


def bytes_of(document: dict) -> bytes:
    return json.dumps(document).encode()


def add(user, kind: str, document: dict) -> kind_files.Report:
    held = kind_files.read(kind, bytes_of(document), "theirs.json")
    return kind_files.apply(user, held)


def plan_of(user, kind: str, document: dict) -> dict:
    return kind_files.plan(user, kind_files.read(kind, bytes_of(document), "theirs.json"))


def outcomes(review: dict) -> list[str]:
    return [row["outcome"] for section in review["sections"] for row in section["rows"]]


# --------------------------------------------------------------------- the round trip


@pytest.mark.parametrize("kind", KINDS)
def test_a_file_goes_from_one_account_to_another_and_nothing_of_the_first_goes_with_it(
    user, other_user, kind
):
    furnish(user)
    furnish_somebody_else = document_of(user, kind)
    text = json.dumps(furnish_somebody_else)
    assert user.email not in text

    review = plan_of(other_user, kind, furnish_somebody_else)
    assert set(outcomes(review)) == {"add"}
    assert Company.objects.for_user(other_user).count() == 0, "reading adds nothing"

    report = add(other_user, kind, furnish_somebody_else)
    assert report.added == len(furnish_somebody_else[kind])

    # What came out of the second account is what went into it, field for field.
    again = document_of(other_user, kind)
    again["postulo"].pop("exported_at")
    original = document_of(user, kind)
    original["postulo"].pop("exported_at")
    if kind == "companies":
        # The second account also holds what the first did not name in the file: nothing.
        assert [row["name"] for row in again[kind]] == [row["name"] for row in original[kind]]
    assert len(again[kind]) >= len(original[kind])
    # And nothing in the second account belongs to the first.
    for model in (Company, Contact, JobPosting, Application):
        assert (
            not model.objects.for_user(user)
            .filter(pk__in=model.objects.for_user(other_user).values("pk"))
            .exists()
        )


@pytest.mark.parametrize("kind", KINDS)
def test_a_second_import_adds_nothing(user, other_user, kind):
    furnish(user)
    document = document_of(user, kind)
    add(other_user, kind, document)
    before = {
        model: model.objects.for_user(other_user).count()
        for model in (Company, Contact, JobPosting, Application, Interview)
    }

    review = plan_of(other_user, kind, document)
    assert "add" not in outcomes(review)
    assert set(outcomes(review)) == {"present"}
    assert add(other_user, kind, document).added == 0
    after = {
        model: model.objects.for_user(other_user).count()
        for model in (Company, Contact, JobPosting, Application, Interview)
    }
    assert after == before


@pytest.mark.parametrize("kind", KINDS)
def test_the_account_a_file_came_from_finds_all_of_it_already_there(user, kind):
    furnish(user)
    review = plan_of(user, kind, document_of(user, kind))
    assert set(outcomes(review)) == {"present"}
    assert review["adds"] is False


def test_a_company_carries_its_departments_industries_and_identifiers(user, other_user):
    furnish(user)
    add(other_user, "companies", document_of(user, "companies"))
    aperture = Company.objects.for_user(other_user).get(name="Aperture Science")
    assert [d.name for d in aperture.departments.all()] == ["Testing"]
    assert [i.name for i in aperture.industries.all()] == ["Research"]
    assert Company.by_identifier(other_user, "wikidata", "Q95") == aperture
    assert aperture.parent.name == "Weyland Holdings"


def test_a_contact_arrives_with_how_to_reach_them_and_the_company_it_was_at(user, other_user):
    furnish(user)
    document = document_of(user, "contacts")
    add(other_user, "contacts", document)
    glados = Contact.objects.for_user(other_user).get(name="Glados")
    assert glados.company.name == "Aperture Science"
    # A number is held by one row on the instance: while the first account has it, the
    # second is not given it, and is not told so either (#142).
    assert not glados.phone_numbers.exists()
    glados.delete()
    PhoneNumber.objects.filter(owner=user).delete()
    add(other_user, "contacts", document)
    glados = Contact.objects.for_user(other_user).get(name="Glados")
    assert [n.number for n in glados.phone_numbers.all()] == ["+351912345678"]
    assert [w.url for w in glados.web_links.all()] == ["https://glados.example"]
    assert Contact.objects.for_user(other_user).get(name="A friend").company is None


def test_an_application_arrives_with_its_timeline_its_interviews_and_where_it_came_from(
    user, other_user
):
    furnish(user)
    add(other_user, "applications", document_of(user, "applications"))
    application = Application.objects.for_user(other_user).get()
    assert application.posting.title == "Test subject"
    assert application.posting.company.name == "Aperture Science"
    assert application.status == Status.APPLIED
    assert application.applied_at is not None
    assert application.interviews.count() == 1
    # Everything the first account's timeline said, and one entry saying which file it is
    # from: provenance is never in doubt.
    theirs = ApplicationEvent.objects.filter(application__owner=user).count()
    mine = ApplicationEvent.objects.filter(application=application)
    assert mine.count() == theirs + 1
    assert mine.filter(summary="Imported from theirs.json").count() == 1
    # It has no reminders, offers or contact of the first account's.
    assert not application.reminders.exists()


def test_a_listing_that_is_already_there_gets_the_application_added_to_it(user, other_user):
    furnish(user)
    add(other_user, "listings", document_of(user, "listings"))
    review = plan_of(other_user, "applications", document_of(user, "applications"))
    assert outcomes(review) == ["add"]
    assert "already yours" in " ".join(review["sections"][0]["rows"][0]["notes"])
    add(other_user, "applications", document_of(user, "applications"))
    assert JobPosting.objects.for_user(other_user).filter(title="Test subject").count() == 1


# --------------------------------------------------------------------- what is the same


def test_a_company_is_the_same_by_name_whatever_the_case_and_by_identifier_whatever_the_name(
    user,
):
    Company.objects.create(owner=user, name="ACME")
    mine = get_or_create_company(user, "Known as something else", wikidata="Q42")
    document = {
        "postulo": {"companies_format": 1},
        "companies": [
            {"name": "acme"},
            {"name": "The Same By Id", "identifiers": [{"scheme": "wikidata", "value": "Q42"}]},
            {"name": "Brand new"},
            {"name": "BRAND NEW"},
        ],
    }
    assert outcomes(plan_of(user, "companies", document)) == [
        "present",
        "present",
        "add",
        "repeated",
    ]
    add(user, "companies", document)
    assert Company.objects.for_user(user).count() == 3
    assert mine.pk in Company.objects.for_user(user).values_list("pk", flat=True)


def test_a_contact_is_the_same_by_email_else_by_name_at_the_company(user):
    here = Company.objects.create(owner=user, name="Here")
    Contact.objects.create(owner=user, name="Ada", email="ada@example.org")
    Contact.objects.create(owner=user, name="Bob", company=here)
    document = {
        "postulo": {"contacts_format": 1},
        "contacts": [
            {"name": "Somebody else", "email": "ADA@example.org"},
            {"name": "bob", "company": "here"},
            {"name": "Bob", "company": "There"},
            {"name": "Bob", "company": "There"},
        ],
    }
    assert outcomes(plan_of(user, "contacts", document)) == [
        "present",
        "present",
        "add",
        "repeated",
    ]
    add(user, "contacts", document)
    assert Contact.objects.for_user(user).filter(name="Bob").count() == 2
    assert Company.objects.for_user(user).filter(name="There").exists()


def test_a_listing_is_the_same_by_address_else_by_company_title_and_date(user):
    company = Company.objects.create(owner=user, name="Here")
    JobPosting.objects.create(owner=user, company=company, title="One", url="https://x.example/1")
    JobPosting.objects.create(
        owner=user, company=company, title="Two", posted_at=dt.date(2026, 1, 2)
    )
    document = {
        "postulo": {"listings_format": 1},
        "listings": [
            {"company": "Here", "title": "Whatever", "url": "http://www.x.example/1/"},
            {"company": "here", "title": "TWO", "posted_at": "2026-01-02"},
            {"company": "Here", "title": "Two", "posted_at": "2026-01-03"},
            {"company": "Here", "title": "Two", "posted_at": "2026-01-03"},
            {"company": "Here", "title": ""},
            {"title": "Orphan"},
        ],
    }
    assert outcomes(plan_of(user, "listings", document)) == [
        "present",
        "present",
        "add",
        "repeated",
        "refused",
        "refused",
    ]


def test_an_application_is_the_same_by_its_listing_and_the_day_it_was_sent(user):
    furnish(user)
    document = document_of(user, "applications")
    row = document["applications"][0]
    other_day = json.loads(json.dumps(document))
    other_day["applications"][0]["applied_at"] = "2020-01-01T10:00:00+00:00"
    assert outcomes(plan_of(user, "applications", document)) == ["present"]
    assert outcomes(plan_of(user, "applications", other_day)) == ["add"]
    assert row["listing"]["title"] == "Test subject"


def test_a_row_the_forms_would_refuse_is_refused_and_the_rest_is_read(user):
    document = {
        "postulo": {"companies_format": 1},
        "companies": [
            {"name": "Fine"},
            {"name": "Bad site", "website": "not an address"},
            {"name": ["a", "list"]},
            {"website": "https://x.example"},
            "not a row",
        ],
    }
    held = kind_files.read("companies", bytes_of(document))
    assert held["unreadable"] == 1
    review = kind_files.plan(user, held)
    assert outcomes(review) == ["add", "refused", "refused", "refused"]
    assert kind_files.apply(user, held).added == 1
    assert list(Company.objects.for_user(user).values_list("name", flat=True)) == ["Fine"]


# ------------------------------------------------------------- what a file may not say


@pytest.mark.parametrize("kind", KINDS)
def test_unknown_keys_an_owner_and_a_foreign_id_write_nothing(user, other_user, kind):
    furnish(other_user)
    theirs = Company.objects.for_user(other_user).get(name="Aperture Science")
    document = document_of(other_user, kind)
    hostile = {
        "owner_id": other_user.pk,
        "owner": other_user.pk,
        "id": theirs.pk,
        "pk": theirs.pk,
        "company_id": theirs.pk,
        "posting_id": 1,
        "created_at": "1999-01-01T00:00:00Z",
    }
    for row in document[kind]:
        row.update(hostile)
        if "listing" in row:
            row["listing"].update(hostile)
    held = kind_files.read(kind, bytes_of(document))
    for row in held["rows"]:
        assert not set(hostile) & set(row)
    before = Company.objects.for_user(other_user).count()
    kind_files.apply(user, held)
    assert Company.objects.for_user(other_user).count() == before
    for model in (Company, Contact, JobPosting, Application):
        assert not model.objects.for_user(user).filter(owner=other_user).exists()
        assert model.objects.filter(owner=user).exclude(owner_id=user.pk).count() == 0


@pytest.mark.parametrize("kind", KINDS)
def test_the_archives_reader_refuses_each_file(user, kind):
    furnish(user)
    data = bytes_of(document_of(user, kind))
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr(export.MANIFEST_NAME, data)
    with zipfile.ZipFile(archive) as zipped:
        with pytest.raises(importer.ArchiveError):
            importer.load(user, zipped)
    assert Company.objects.for_user(user).count() == 1 + 1  # what was furnished, no more


def test_each_file_refuses_the_others_and_the_candidates_and_the_archives(user):
    furnish(user)
    for kind in KINDS:
        for other in KINDS:
            if other == kind:
                continue
            with pytest.raises(kind_files.Refused):
                kind_files.read(kind, bytes_of(document_of(user, other)))
    with pytest.raises(kind_files.Refused):
        kind_files.read("companies", bytes_of(export.build_candidate_document(user)))
    with pytest.raises(kind_files.Refused):
        kind_files.read("companies", bytes_of(export.build_document(user)))
    for rubbish in (b"", b"[]", b"not json", b'{"postulo": {"companies_format": 0}}'):
        with pytest.raises(kind_files.Refused):
            kind_files.read("companies", rubbish)
    with pytest.raises(kind_files.Refused):
        kind_files.read("companies", b'{"postulo": {"companies_format": 1}}')


def test_the_candidates_reader_refuses_a_file_of_one_kind(user):
    furnish(user)
    with pytest.raises(candidate.Refused):
        candidate.read(bytes_of(document_of(user, "companies")))


def test_a_file_written_by_a_newer_postulo_is_read_so_far_and_says_so(user):
    document = {"postulo": {"companies_format": 9}, "companies": [{"name": "Fine"}]}
    review = plan_of(user, "companies", document)
    assert outcomes(review) == ["add"]
    assert any("newer Postulo" in note for note in review["notes"])


# ----------------------------------------------------------------------------- shapes


def fingerprint(kind: str) -> str:
    """What each file's blocks look like, borrowed ones included."""
    shape = {
        "schema": kind_files.SCHEMAS[kind],
        "borrowed": {
            "companies": export.COMPANY_FIELDS,
            "contacts": [
                export.CONTACT_FIELDS,
                export.PHONE_NUMBER_FIELDS,
                export.POSTAL_ADDRESS_FIELDS,
                export.WEB_LINK_FIELDS,
                export.MESSAGING_FIELDS,
            ],
            "listings": export.POSTING_FIELDS,
            "applications": [
                export.APPLICATION_FIELDS,
                export.POSTING_FIELDS,
                export.EVENT_FIELDS,
                export.INTERVIEW_FIELDS,
            ],
        }[kind],
    }
    return hashlib.sha256(json.dumps(shape, sort_keys=True).encode()).hexdigest()[:16]


#: What each version of each file looked like. A new shape is a new line, and a new format.
SHAPES = {
    "companies": {1: "21618cc7975ef9fb", 2: "2f357806aee6ae96"},
    "contacts": {1: "2147065eb442ef0d"},
    "listings": {1: "10895cbc17c8b6cd"},
    "applications": {1: "4a452b4efb03e98c"},
}


@pytest.mark.parametrize("kind", KINDS)
def test_a_block_that_changes_shape_is_a_new_version_of_its_file(kind):
    """The files borrow the archive's blocks, so a field added to one of them for the
    archive's sake changes a file too -- and its reader has to be told.

    Fails when a block changes and nobody has decided. Bump the kind's format in
    `kind_files.FORMATS`, teach `read` the older shape as well, and record the new one here.
    """
    assert SHAPES[kind].get(kind_files.FORMATS[kind]) == fingerprint(kind), (
        f"the {kind} file's blocks changed shape (now {fingerprint(kind)}) and its format "
        f"is still {kind_files.FORMATS[kind]}"
    )


def test_what_a_file_writes_is_something_the_archive_writes():
    assert set(kind_files.COMPANY_FIELDS) <= set(export.COMPANY_FIELDS)
    assert set(kind_files.CONTACT_FIELDS) <= set(export.CONTACT_FIELDS)
    assert set(kind_files.LISTING_FIELDS) <= set(export.POSTING_FIELDS)
    assert set(kind_files.APPLICATION_FIELDS) <= set(export.APPLICATION_FIELDS)
    assert set(kind_files.EVENT_FIELDS) <= set(export.EVENT_FIELDS)
    assert set(kind_files.INTERVIEW_FIELDS) <= set(export.INTERVIEW_FIELDS)
    # A number's confirmation and its being a way back in are the one thing not written.
    assert set(export.PHONE_NUMBER_FIELDS) - set(
        kind_files.CONTACT_DETAILS["phone_numbers"]
    ) == set(kind_files.NOT_THE_FILES_TO_SAY)


def test_the_archive_and_the_candidate_file_are_untouched_by_the_new_files():
    assert export.FORMAT_VERSION == 49
    assert export.CANDIDATE_FORMAT == 11
    assert set(kind_files.FORMATS.values()) == {1, 2}


def test_every_kind_is_named_in_the_settings_section_it_belongs_to():
    from postulo.core import settings_sections

    data = next(section for section in settings_sections.BUILTIN if section.slug == "data")
    for kind in KINDS:
        assert f"core:file_{kind}" in data.match
        assert f"core:file_{kind}_download" in data.match


# --------------------------------------------------------------------------- the pages


def upload(document) -> SimpleUploadedFile:
    return SimpleUploadedFile("theirs.json", bytes_of(document), content_type="application/json")


@pytest.mark.parametrize("kind", KINDS)
def test_the_page_offers_the_file_reads_one_and_adds_it_when_told(client, user, other_user, kind):
    furnish(other_user)
    document = document_of(other_user, kind)
    client.force_login(user)
    address = reverse(f"core:file_{kind}")

    assert client.get(address).status_code == 200
    download = client.get(reverse(f"core:file_{kind}_download"))
    assert download.status_code == 200
    assert "attachment" in download["Content-Disposition"]
    assert download["Cache-Control"] == "private, max-age=0, no-store"
    assert json.loads(download.content)[kind] == []

    assert client.post(address, {"file": upload(document)}).status_code == 302
    page = client.get(address)
    assert b"What is in the file" in page.content
    assert b'data-outcome="add"' in page.content
    assert not Company.objects.for_user(user).exists(), "reading a file adds nothing"

    assert client.post(address, {"action": "confirm"}).status_code == 302
    assert Company.objects.for_user(user).exists()
    again = client.get(address)
    assert b"What is in the file" not in again.content


def test_the_data_page_counts_each_kind(client, user):
    furnish(user)
    client.force_login(user)
    page = client.get(reverse("core:export")).content.decode()
    for kind in KINDS:
        assert f'data-kind-file="{kind}"' in page
        assert reverse(f"core:file_{kind}") in page


def test_a_file_is_forgotten_when_somebody_starts_again(client, user):
    client.force_login(user)
    address = reverse("core:file_companies")
    client.post(
        address,
        {"file": upload({"postulo": {"companies_format": 1}, "companies": [{"name": "X"}]})},
    )
    assert b"What is in the file" in client.get(address).content
    client.post(address, {"action": "forget"})
    assert b"What is in the file" not in client.get(address).content
    assert client.post(address, {"action": "confirm"}).status_code == 302
    assert not Company.objects.for_user(user).exists()


def test_the_contacts_file_costs_the_same_queries_however_many_contacts(user):
    """Their numbers, addresses, links and handles are fetched together, not per person."""
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    def queries() -> int:
        with CaptureQueriesContext(connection) as captured:
            kind_files.build_document(user, "contacts")
        return len(captured)

    Contact.objects.create(owner=user, name="First")
    few = queries()
    for number in range(10):
        Contact.objects.create(owner=user, name=f"Person {number}")
    assert queries() == few
