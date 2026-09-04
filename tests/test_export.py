"""Taking everything out, and putting it back.

The round trip is the test that matters. An export that cannot be imported is a
souvenir, and the only way to know it is a copy is to rebuild from it and compare.
"""

import datetime as dt
import json
import zipfile
from io import BytesIO

import pytest
from django.contrib.contenttypes.models import ContentType
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from postulo.applications.models import Application, ApplicationEvent, Reminder, Status
from postulo.applications.services import change_status, record_event
from postulo.core import export as export_module
from postulo.core import importer
from postulo.core.models import Tag
from postulo.documents.models import CV, CoverLetter, CVItem, UploadedDocument
from postulo.jobs.models import Company, Contact, JobPosting
from postulo.resume.models import Experience, Skill, SkillGroup


@pytest.fixture
def populated(db, user):
    """One small but complete job search, touching every kind of record."""
    profile = user.profile
    profile.headline = "Backend engineer"
    profile.location = "Paris"
    profile.save()
    user.first_name, user.last_name = "Tiago", "Agueda"
    user.save()

    tag = Tag.objects.create(owner=user, name="Dream job")

    experience = Experience.objects.create(
        owner=user,
        organisation="Aperture Science",
        role="Senior Engineer",
        start_date=dt.date(2021, 3, 1),
        highlights="Cut deploy time.\nMentored three engineers.",
    )
    group = SkillGroup.objects.create(owner=user, name="Languages")
    Skill.objects.create(owner=user, group=group, name="Python")

    company = Company.objects.create(owner=user, name="Black Mesa", location="Paris")
    contact = Contact.objects.create(owner=user, company=company, name="A Recruiter")
    posting = JobPosting.objects.create(
        owner=user, company=company, title="Research Engineer", source="Referral"
    )
    application = Application.objects.create(
        owner=user, posting=posting, status=Status.DRAFT, contact=contact
    )
    application.tags.set([tag])
    change_status(application, Status.APPLIED)
    record_event(application, summary="Spoke to the recruiter")
    Reminder.objects.create(
        owner=user, application=application, summary="Chase", due_at=timezone.now()
    )

    cv = CV.objects.create(owner=user, name="Backend EN", headline="Backend engineer")
    CVItem.objects.create(
        owner=user,
        cv=cv,
        content_type=ContentType.objects.get_for_model(Experience),
        object_id=experience.pk,
        override_highlights="Tailored for this one.",
    )
    CoverLetter.objects.create(owner=user, name="General", body="Dear {{ company }},")
    UploadedDocument.objects.create(
        owner=user,
        title="Designed CV",
        file=SimpleUploadedFile("designed.pdf", b"%PDF-1.7 pretend"),
    )
    return user


def read_archive(user) -> tuple[zipfile.ZipFile, dict]:
    buffer = export_module.write_archive(user)
    archive = zipfile.ZipFile(buffer)
    return archive, json.loads(archive.read(export_module.MANIFEST_NAME))


# ----------------------------------------------------------------- the document


def test_the_export_is_one_readable_json_document_plus_files(populated):
    archive, document = read_archive(populated)

    assert export_module.MANIFEST_NAME in archive.namelist()
    assert any(name.startswith("media/") for name in archive.namelist())
    assert document["postulo"]["format"] == export_module.FORMAT_VERSION


def test_records_are_nested_the_way_they_relate(populated):
    """Not a flat dump of tables: the point is that somebody can read it in ten years."""
    _archive, document = read_archive(populated)

    company = document["companies"][0]
    posting = company["postings"][0]
    application = posting["applications"][0]

    assert company["name"] == "Black Mesa"
    assert posting["title"] == "Research Engineer"
    assert application["status"] == Status.APPLIED
    assert any(event["to_status"] == Status.APPLIED for event in application["events"])
    assert application["reminders"][0]["summary"] == "Chase"
    assert application["tags"] == ["dream-job"]


def test_everything_the_account_holds_is_present(populated):
    _archive, document = read_archive(populated)

    assert document["account"]["profile"]["headline"] == "Backend engineer"
    assert document["resume"]["experience"][0]["role"] == "Senior Engineer"
    assert document["resume"]["skills"][0]["name"] == "Python"
    assert document["documents"]["cvs"][0]["name"] == "Backend EN"
    assert document["documents"]["cvs"][0]["entries"][0]["kind"] == "experience"
    assert document["documents"]["cover_letters"][0]["name"] == "General"
    assert document["documents"]["uploads"][0]["title"] == "Designed CV"


def test_an_export_holds_nothing_belonging_to_anyone_else(populated, other_user):
    Company.objects.create(owner=other_user, name="Umbrella Corporation")

    _archive, document = read_archive(populated)

    assert [c["name"] for c in document["companies"]] == ["Black Mesa"]


def test_a_missing_file_costs_that_file_and_nothing_else(populated):
    """A record whose file has vanished must not cost you the whole export."""
    upload = UploadedDocument.objects.for_user(populated).get()
    upload.file.storage.delete(upload.file.name)

    archive, document = read_archive(populated)

    assert document["documents"]["uploads"][0]["title"] == "Designed CV"
    assert export_module.MANIFEST_NAME in archive.namelist()


# ------------------------------------------------------------------ round trip


def test_an_export_can_be_imported_into_an_empty_account(populated, other_user):
    archive, _document = read_archive(populated)

    report = importer.load(other_user, archive)

    assert report.companies == 1
    assert report.applications == 1
    assert report.cvs == 1
    assert Company.objects.for_user(other_user).get().name == "Black Mesa"


def test_the_round_trip_preserves_what_matters(populated, other_user):
    archive, _document = read_archive(populated)
    importer.load(other_user, archive)

    application = Application.objects.for_user(other_user).select_related("posting").get()

    assert application.posting.title == "Research Engineer"
    assert application.posting.company.name == "Black Mesa"
    assert application.status == Status.APPLIED
    assert application.applied_at is not None
    assert application.contact.name == "A Recruiter"
    assert [tag.name for tag in application.tags.all()] == ["Dream job"]


def test_the_timeline_survives_the_round_trip(populated, other_user):
    """A copy without the history would be a list, not a record."""
    archive, _document = read_archive(populated)
    importer.load(other_user, archive)

    application = Application.objects.for_user(other_user).get()
    summaries = [event.summary for event in application.events.all()]

    assert "Spoke to the recruiter" in summaries
    assert application.events.filter(to_status=Status.APPLIED).exists()


def test_a_cv_still_points_at_the_right_career_entry(populated, other_user):
    """Identifiers in the file are local to it, so every reference is remapped."""
    archive, _document = read_archive(populated)
    importer.load(other_user, archive)

    item = CVItem.objects.for_user(other_user).get()
    experience = Experience.objects.for_user(other_user).get()

    assert item.object_id == experience.pk
    assert item.item == experience
    assert item.override_highlights == "Tailored for this one."
    assert item.cv.owner == other_user


def test_a_skill_still_belongs_to_its_group(populated, other_user):
    archive, _document = read_archive(populated)
    importer.load(other_user, archive)

    skill = Skill.objects.for_user(other_user).get()

    assert skill.group is not None
    assert skill.group.name == "Languages"


def test_files_come_back_with_their_contents(populated, other_user):
    archive, _document = read_archive(populated)
    importer.load(other_user, archive)

    upload = UploadedDocument.objects.for_user(other_user).get()
    upload.file.open("rb")
    try:
        assert upload.file.read() == b"%PDF-1.7 pretend"
    finally:
        upload.file.close()


def test_importing_into_an_account_that_already_has_data_is_refused(populated):
    """Merging is a judgement Postulo is not in a position to make."""
    archive, _document = read_archive(populated)

    with pytest.raises(importer.ArchiveError, match="already holds a job search"):
        importer.load(populated, archive)


def test_it_can_be_forced_when_a_duplicate_is_what_you_want(populated):
    """Forcing duplicates the work, not the employers.

    A company is an identity keyed by its name — the same rule intake uses — so an
    import attaches to the one that is already there. The applications underneath are
    what get a second copy.
    """
    archive, _document = read_archive(populated)

    importer.load(populated, archive, force=True)

    assert Company.objects.for_user(populated).count() == 1, "one employer, not two"
    assert Application.objects.for_user(populated).count() == 2
    assert CV.objects.for_user(populated).count() == 2
    assert sorted(CV.objects.for_user(populated).values_list("name", flat=True)) == [
        "Backend EN",
        "Backend EN (2)",
    ]


def test_a_failed_import_leaves_the_account_untouched(populated, other_user, monkeypatch):
    """One transaction: a broken file must not leave half a job search behind."""
    archive, _document = read_archive(populated)

    def explode(*args, **kwargs):
        raise RuntimeError("something went wrong half way through")

    monkeypatch.setattr(CoverLetter.objects, "create", explode)

    with pytest.raises(RuntimeError):
        importer.load(other_user, archive)

    assert not Company.objects.for_user(other_user).exists()
    assert not Application.objects.for_user(other_user).exists()
    assert not Experience.objects.for_user(other_user).exists()


@pytest.mark.parametrize(
    "contents,message",
    [
        ({"something.txt": "not an export"}, "No postulo.json"),
        ({"postulo.json": "{ broken"}, "not valid JSON"),
        ({"postulo.json": '{"hello": "world"}'}, "does not look like a Postulo export"),
    ],
)
def test_something_that_is_not_an_export_is_refused_clearly(db, user, contents, message):
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, body in contents.items():
            archive.writestr(name, body)
    buffer.seek(0)

    with pytest.raises(importer.ArchiveError, match=message):
        importer.load(user, zipfile.ZipFile(buffer))


# ------------------------------------------------------------------- the views


def test_the_export_page_says_what_is_in_the_archive(client, populated):
    client.force_login(populated)
    response = client.get(reverse("core:export"))

    assert response.status_code == 200
    assert response.context["counts"]["applications"] == 1


def test_downloading_needs_a_post(client, populated):
    """Reading every record and file an account owns is not something a link should do."""
    client.force_login(populated)

    assert client.get(reverse("core:export_download")).status_code == 405


def test_the_download_is_a_usable_archive(client, populated):
    client.force_login(populated)
    response = client.post(reverse("core:export_download"))
    try:
        assert response.status_code == 200
        assert response["Content-Disposition"].startswith("attachment;")
        body = b"".join(response.streaming_content)
    finally:
        response.close()

    with zipfile.ZipFile(BytesIO(body)) as archive:
        document = json.loads(archive.read(export_module.MANIFEST_NAME))

    assert document["companies"][0]["name"] == "Black Mesa"


def test_exporting_requires_signing_in(client, db):
    assert client.get(reverse("core:export")).status_code == 302


def test_events_are_not_lost_when_an_application_moved_several_times(populated, other_user):
    application = Application.objects.for_user(populated).get()
    change_status(application, Status.INTERVIEWING)
    change_status(application, Status.REJECTED)
    before = ApplicationEvent.objects.for_user(populated).count()

    archive, _document = read_archive(populated)
    importer.load(other_user, archive)

    assert ApplicationEvent.objects.for_user(other_user).count() == before
