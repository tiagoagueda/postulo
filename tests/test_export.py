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
from django.core.cache import cache
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
from postulo.resume.models import Experience, Link, Publication, Skill, SkillGroup


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


def test_the_kind_of_a_company_travels_and_an_older_archive_is_employers(populated, other_user):
    """Format 16 writes the kind (#202); an archive from before it named none, and every
    company in it was recorded as an employer, which is what it stays."""
    from postulo.jobs.models import CompanyKind

    Company.objects.create(
        owner=populated, name="France Travail", kind=CompanyKind.EMPLOYMENT_SERVICE
    )
    _archive, document = read_archive(populated)
    assert document["postulo"]["format"] >= 16, "the kind arrived with format 16"
    kinds = {row["name"]: row["kind"] for row in document["companies"]}
    assert kinds == {"Black Mesa": "employer", "France Travail": "employment_service"}

    importer.load(other_user, zipfile.ZipFile(export_module.write_archive(populated)))
    restored = {c.name: c.kind for c in Company.objects.for_user(other_user)}
    assert restored == {"Black Mesa": "employer", "France Travail": "employment_service"}

    # The same archive, written the way format 15 wrote it: no kind anywhere, and one
    # that is not a kind, which a stranger's file could say.
    document["postulo"]["format"] = 15
    document["companies"][0].pop("kind")
    document["companies"][1]["kind"] = "planet"
    Company.objects.for_user(other_user).delete()
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("postulo.json", json.dumps(document, default=str))
    buffer.seek(0)
    importer.load(other_user, zipfile.ZipFile(buffer), force=True)
    assert {c.kind for c in Company.objects.for_user(other_user)} == {"employer"}


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


def _archive_with_format(fmt) -> BytesIO:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("postulo.json", json.dumps({"postulo": {"format": fmt}}))
    buffer.seek(0)
    return buffer


def test_an_archive_from_a_newer_postulo_is_refused_naming_both_formats(db, user):
    newer = export_module.FORMAT_VERSION + 1
    with pytest.raises(importer.ArchiveError) as caught:
        importer.load(user, zipfile.ZipFile(_archive_with_format(newer)))
    assert str(newer) in str(caught.value)
    assert f"1 to {export_module.FORMAT_VERSION}" in str(caught.value)


@pytest.mark.parametrize("fmt", ["25", True, 0, 1.5, None])
def test_a_format_that_is_not_a_known_number_is_refused(db, user, fmt):
    with pytest.raises(importer.ArchiveError, match="this version of Postulo reads"):
        importer.load(user, zipfile.ZipFile(_archive_with_format(fmt)))


def test_import_data_says_so_rather_than_a_traceback_for_a_newer_archive(db, user, tmp_path):
    from django.core.management import CommandError, call_command

    path = tmp_path / "newer.zip"
    path.write_bytes(_archive_with_format(export_module.FORMAT_VERSION + 1).getvalue())
    with pytest.raises(CommandError, match="this version of Postulo reads"):
        call_command("import_data", user.email, str(path))


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
    """Two steps now: the press asks for the archive, the page it lands on has the link.

    With no worker configured -- which is the default, and what these tests run under --
    the work is done where the request stands, so the page is already finished when it is
    drawn and the only difference is the extra click (#247).
    """
    from postulo.core.models import ExportArchive

    client.force_login(populated)
    asked = client.post(reverse("core:export_download"))
    assert asked.status_code == 302

    page = client.get(asked.url).content.decode()
    archive_row = ExportArchive.objects.for_user(populated).get()
    link = reverse("core:export_archive", args=[archive_row.pk])
    assert link in page, "the finished page points at the file"

    response = client.get(link)
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


# --------------------------------------------- what a page says is in it (#220)


def test_the_counts_agree_with_the_document_they_used_to_be_measured_from(populated):
    """The two answers have to be the same answer, or one page is lying about the other.

    Both pages that say what an export holds used to build the whole document and measure
    its lists: every record the account owns, read and nested, so that eight numbers could
    be printed -- and on SQLite that was done holding the write lock (#220). They count now,
    and this is what holds the counting to what the archive actually carries.
    """
    _archive, document = read_archive(populated)

    assert export_module.counts(populated) == document["counts"]


def test_the_counts_are_counted_rather_than_assembled(populated):
    """Eight `COUNT(*)` queries, and not one that reads a row."""
    from django.db import connection

    def only_counts(execute, sql, params, many, context):
        assert "COUNT(*)" in sql.upper(), f"not a count: {sql}"
        return execute(sql, params, many, context)

    with connection.execute_wrapper(only_counts):
        export_module.counts(populated)


def test_the_export_page_does_not_build_the_archive_to_show_the_numbers(client, populated):
    """The page a person opens before deciding should not do the expensive thing first."""
    from unittest import mock

    client.force_login(populated)

    def refuse(user):
        raise AssertionError("the overview built the whole document to print six numbers")

    with mock.patch.object(export_module, "build_document", refuse):
        response = client.get(reverse("core:export"))

    assert response.status_code == 200
    assert b"Build the archive" in response.content


# ------------------------------------------- a document's language travels (#283)


@pytest.mark.django_db
def test_an_uploads_and_a_snapshots_language_survive_the_round_trip(user, other_user):
    """Format 17. An archive written before it has no such key, and must still restore --
    which it does, blank, because blank is what "nobody has said" looks like."""
    import json
    import zipfile
    from io import BytesIO

    from django.core.files.base import ContentFile

    from postulo.core import export as export_module
    from postulo.core import importer
    from postulo.documents.models import DocumentKind, RenderedDocument, UploadedDocument

    upload = UploadedDocument(
        owner=user, title="Diploma", kind=DocumentKind.CERTIFICATE, language="de"
    )
    upload.file.save("diploma.pdf", ContentFile(b"%PDF-1.7 x"), save=True)
    sent = RenderedDocument(
        owner=user, title="CV", kind=DocumentKind.CV, language="fr-FR", checksum="abc"
    )
    sent.file.save("cv.pdf", ContentFile(b"%PDF-1.7 y"), save=True)

    document = export_module.build_document(user)
    assert document["postulo"]["format"] >= 17, "the language arrived with format 17"
    assert document["documents"]["uploads"][0]["language"] == "de"
    assert document["documents"]["sent"][0]["language"] == "fr-FR"

    importer.load(other_user, zipfile.ZipFile(export_module.write_archive(user)))
    assert UploadedDocument.objects.for_user(other_user).get().language == "de"
    assert RenderedDocument.objects.for_user(other_user).get().language == "fr-FR"

    # The same archive as format 16 wrote it: no language anywhere.
    UploadedDocument.objects.for_user(other_user).delete()
    RenderedDocument.objects.for_user(other_user).delete()
    document["postulo"]["format"] = 16
    document["documents"]["uploads"][0].pop("language")
    document["documents"]["sent"][0].pop("language")
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("postulo.json", json.dumps(document, default=str))
    buffer.seek(0)

    importer.load(other_user, zipfile.ZipFile(buffer))

    assert UploadedDocument.objects.for_user(other_user).get().language == ""
    assert RenderedDocument.objects.for_user(other_user).get().language == ""


def test_a_link_on_a_cv_comes_back_with_it(populated, other_user):
    """The importer once kept its own map of what a CV can hold and left the links out (#470)."""
    link = Link.objects.create(owner=populated, title="Portfolio", url="https://example.com/me")
    CVItem.objects.create(
        owner=populated,
        cv=CV.objects.for_user(populated).get(),
        content_type=ContentType.objects.get_for_model(Link),
        object_id=link.pk,
    )
    archive, _document = read_archive(populated)
    report = importer.load(other_user, archive)

    restored = Link.objects.for_user(other_user).get()
    kept = CVItem.objects.for_user(other_user).filter(
        content_type=ContentType.objects.get_for_model(Link)
    )
    assert [item.object_id for item in kept] == [restored.pk]
    assert report.skipped == []


# ------------------------------------------- every preference travels, off as well as on (#464)


@pytest.mark.django_db
def test_every_exported_preference_survives_the_round_trip(user, other_user):
    """A switch turned off and a dashboard cleared are values like any other: the importer
    used to skip every falsy one, so they came back as the defaults."""
    from postulo.plugins.policy import GOVERNED_KINDS
    from postulo.plugins.registry import plugins

    installed = sorted({p.name for kind in GOVERNED_KINDS for p in plugins(kind)})
    assert installed, "the test needs one governed plugin to switch off"

    chosen = {
        "keyboard_shortcuts": False,
        "show_key_hints": False,
        "nav_underline": False,
        "density": "compact",
        "plugins_off": installed[:1],
        "keep_page_source": True,
        "keep_page_rendering": True,
        "closing_notice_days": 10,
        "dashboard_widgets": [],
        "quiet_after_days": 40,
        "use_gravatar": True,
        "show_career_order": not user.profile.show_career_order,
    }
    for name, value in chosen.items():
        assert getattr(other_user.profile, name) != value, f"{name} must differ from the default"
        setattr(user.profile, name, value)
    user.profile.save()

    importer.load(other_user, zipfile.ZipFile(export_module.write_archive(user)))

    other_user.profile.refresh_from_db()
    for name, value in chosen.items():
        assert getattr(other_user.profile, name) == value, name


@pytest.mark.django_db
def test_a_preference_the_file_gets_wrong_is_not_believed(user):
    document = export_module.build_document(user)
    document["account"]["profile"].update(
        density="enormous",
        closing_notice_days=9999,
        keyboard_shortcuts="no",
        plugins_off=["no-such-plugin"],
    )
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("postulo.json", json.dumps(document, default=str))
    user.profile.density = "comfortable"
    user.profile.save()
    importer.load(user, zipfile.ZipFile(buffer), force=True)
    user.profile.refresh_from_db()
    assert user.profile.density == "comfortable"
    assert user.profile.closing_notice_days == 3
    assert user.profile.keyboard_shortcuts is True
    assert user.profile.plugins_off == []


def test_a_reminder_about_no_application_comes_back_about_none(populated, other_user):
    """It used to travel only inside an application, so one about none was in no archive (#334)."""
    Reminder.objects.create(
        owner=populated, application=None, summary="Renew passport", due_at=timezone.now()
    )
    archive, document = read_archive(populated)
    assert [row["summary"] for row in document["reminders"]] == ["Renew passport"]

    report = importer.load(other_user, archive)

    assert report.reminders == 2
    mine = Reminder.objects.for_user(other_user)
    assert mine.get(summary="Chase").application_id is not None
    assert mine.get(summary="Renew passport").application_id is None


def test_an_archive_from_before_reminders_about_none_still_imports(populated, other_user):
    _archive, document = read_archive(populated)
    document.pop("reminders")
    document["postulo"]["format"] = 25
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as older:
        older.writestr(export_module.MANIFEST_NAME, json.dumps(document))

    report = importer.load(other_user, zipfile.ZipFile(buffer))

    assert report.reminders == 1
    assert Reminder.objects.for_user(other_user).get().summary == "Chase"


def test_the_files_sent_with_an_application_and_where_a_sent_document_went_come_back(
    populated, other_user
):
    """Both used to be left out of the archive (#469)."""
    from django.core.files.base import ContentFile

    from postulo.documents.models import DocumentKind, RenderedDocument

    application = Application.objects.for_user(populated).get()
    upload = UploadedDocument.objects.for_user(populated).get()
    application.sent_uploads.set([upload])
    # A sent document whose application was deleted: only `sent_to` still says where it went.
    sent = RenderedDocument(
        owner=populated,
        title="CV",
        kind=DocumentKind.CV,
        sent_to="Research Engineer at Black Mesa",
    )
    sent.file.save("cv.pdf", ContentFile(b"%PDF-1.7 y"), save=True)
    assert sent.went_with_an_application

    archive, document = read_archive(populated)
    assert document["companies"][0]["postings"][0]["applications"][0]["sent_upload_ids"] == [
        upload.pk
    ]

    importer.load(other_user, archive)

    restored = Application.objects.for_user(other_user).get()
    assert [u.title for u in restored.sent_uploads.all()] == ["Designed CV"]
    assert restored.sent_uploads.get().owner_id == other_user.pk
    came_back = RenderedDocument.objects.for_user(other_user).get()
    assert came_back.sent_to == "Research Engineer at Black Mesa"
    assert came_back.went_with_an_application


def test_an_archive_without_sent_files_or_sent_to_still_imports(populated, other_user):
    _archive, document = read_archive(populated)
    for company in document["companies"]:
        for posting in company["postings"]:
            for application in posting["applications"]:
                application.pop("sent_upload_ids")
    document["postulo"]["format"] = 30
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as older:
        older.writestr(export_module.MANIFEST_NAME, json.dumps(document))

    importer.load(other_user, zipfile.ZipFile(buffer))

    assert Application.objects.for_user(other_user).get().sent_uploads.count() == 0


def test_an_expired_archive_is_deleted_by_the_export_page_without_the_scheduler(
    client, populated, settings, django_capture_on_commit_callbacks
):
    """The deletion used to wait for an optional scheduler; the page promised a day (#539)."""
    from postulo.core.models import ExportArchive

    client.force_login(populated)
    client.post(reverse("core:export_download"))
    archive = ExportArchive.objects.for_user(populated).get()
    stored = archive.file.name
    assert archive.file.storage.exists(stored)

    ExportArchive.objects.filter(pk=archive.pk).update(
        expires_at=timezone.now() - dt.timedelta(hours=1)
    )
    # Tidied at most once an hour: while the press just now has, the page leaves it be.
    assert client.get(reverse("core:export")).status_code == 200
    assert ExportArchive.objects.filter(pk=archive.pk).exists()
    # An hour later.
    cache.delete("core:tidied-up")
    # The file goes when the deletion commits (#355), which a test inside a transaction runs.
    with django_capture_on_commit_callbacks(execute=True):
        response = client.get(reverse("core:export"))

    assert response.status_code == 200
    assert not ExportArchive.objects.filter(pk=archive.pk).exists()
    assert not archive.file.storage.exists(stored)


def test_an_archive_whose_file_is_gone_says_it_expired(client, populated, rf):
    """A row that still names a file the disk no longer has is not a bare 404 (#355)."""
    from django.http import Http404

    from postulo.core import views_export
    from postulo.core.models import ExportArchive

    client.force_login(populated)
    client.post(reverse("core:export_download"))
    archive = ExportArchive.objects.for_user(populated).get()
    archive.file.storage.delete(archive.file.name)

    request = rf.get("/")
    request.user = populated
    with pytest.raises(Http404, match="That export has expired"):
        views_export.export_archive(request, archive.pk)


# ------------------------------------------------------------- the query count


def _grow(user, label: str, how_many: int) -> None:
    """``how_many`` companies, each with a contact reached three ways, an application with
    a tag and an offer, and a sent document with a copy; plus a contact at no company."""
    from django.core.files.base import ContentFile

    from postulo.applications.models import Offer
    from postulo.core.models import PhoneNumber, PostalAddress, WebLink
    from postulo.documents.models import DocumentCopy, RenderedDocument
    from postulo.jobs.models import Department

    contact_type = ContentType.objects.get_for_model(Contact)
    cv = CV.objects.filter(owner=user).first()
    tag = Tag.objects.for_user(user).first()
    people = [Contact.objects.create(owner=user, name=f"{label} loner")]
    for n in range(how_many):
        company = Company.objects.create(owner=user, name=f"{label} {n}")
        department = Department.objects.create(owner=user, company=company, name="R&D")
        people.append(
            Contact.objects.create(
                owner=user, company=company, department=department, name=f"{label} {n}"
            )
        )
        posting = JobPosting.objects.create(owner=user, company=company, title="Engineer")
        application = Application.objects.create(
            owner=user, posting=posting, status=Status.DRAFT, contact=people[-1]
        )
        application.tags.set([tag])
        Offer.objects.create(owner=user, application=application, base_amount=1000)
        sent = RenderedDocument.objects.create(
            owner=user,
            title="Sent",
            kind="cv",
            source=cv,
            application=application,
            file=ContentFile(b"%PDF-1.7 sent", name="sent.pdf"),
            checksum="x",
        )
        DocumentCopy.objects.create(
            owner=user, store="paperless", label="Paperless", document=sent, status="sent"
        )
    for contact in people:
        held = {"content_type": contact_type, "object_id": contact.pk, "owner": user}
        PhoneNumber.objects.create(number=f"+33 1 23 {contact.pk:06d}", **held)
        PostalAddress.objects.create(**held)
        WebLink.objects.create(url="https://example.org/", kind="website", **held)


def _restored(document, user):
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as written:
        written.writestr("postulo.json", json.dumps(document))
    buffer.seek(0)
    return importer.load(user, zipfile.ZipFile(buffer))


def test_the_career_link_and_the_marker_survive_the_round_trip(user, other_user):
    kept = Company.objects.create(owner=user, name="Acme")
    former = Company.objects.create(owner=user, name="Old Employer", from_career=True)
    for company, text in ((kept, "Acme Ltd"), (former, "Old Employer")):
        Experience.objects.create(
            owner=user,
            organisation=text,
            company=company,
            role="Dev",
            start_date=dt.date(2020, 1, 1),
        )
    Experience.objects.create(
        owner=user, organisation="Unlinked", role="Dev", start_date=dt.date(2019, 1, 1)
    )
    archive, document = read_archive(user)
    assert {row["organisation"]: row["company"] for row in document["resume"]["experience"]} == {
        "Acme Ltd": "Acme",
        "Old Employer": "Old Employer",
        "Unlinked": "",
    }

    importer.load(other_user, archive)

    links = {e.organisation: e.company for e in Experience.objects.for_user(other_user)}
    assert links["Acme Ltd"].name == "Acme"
    assert links["Old Employer"].from_career is True
    assert links["Old Employer"].owner == other_user
    assert links["Unlinked"] is None
    assert Company.objects.get(owner=other_user, name="Acme").from_career is False


def test_an_older_archive_restores_every_entry_unlinked(user, other_user):
    Company.objects.create(owner=user, name="Acme")
    Experience.objects.create(
        owner=user, organisation="Acme", role="Dev", start_date=dt.date(2020, 1, 1)
    )
    _archive, document = read_archive(user)
    document["postulo"]["format"] = 44
    for entry in document["resume"]["experience"]:
        del entry["company"]
    for company in document["companies"]:
        del company["from_career"]

    _restored(document, other_user)

    assert Experience.objects.for_user(other_user).get().company is None
    assert Company.objects.get(owner=other_user).from_career is False


def test_a_name_the_archive_does_not_hold_leaves_the_entry_unlinked_and_adds_nothing(
    user, other_user
):
    Experience.objects.create(
        owner=user, organisation="Acme", role="Dev", start_date=dt.date(2020, 1, 1)
    )
    _archive, document = read_archive(user)
    document["resume"]["experience"][0]["company"] = "Somebody Else's"

    _restored(document, other_user)

    assert Experience.objects.for_user(other_user).get().company is None
    assert not Company.objects.for_user(other_user).exists()


def test_the_archive_costs_the_same_queries_however_much_the_account_holds(populated):
    """The build runs inside the write lock, so a query per application, per contact and
    per sent document is every other request waiting on it (#557)."""
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    def count() -> int:
        with CaptureQueriesContext(connection) as queries:
            export_module.build_document(populated)
        return len(queries)

    _grow(populated, "small", 2)
    count()  # the first build fills the content-type cache, which is not the growth
    small = count()
    _grow(populated, "large", 20)
    large = count()

    assert large == small


def test_a_cv_holding_a_publication_and_a_link_keeps_both(populated, other_user):
    """Both are written by model name and read back by the one map (#687, #470)."""
    paper = Publication.objects.create(
        owner=populated, entry_type="book", title="A book", authors="Morgan, Alex", date="2020"
    )
    link = Link.objects.create(owner=populated, title="Site", url="https://example.org/")
    cv = CV.objects.for_user(populated).get()
    for order, entry in enumerate((paper, link), start=5):
        CVItem.objects.create(
            owner=populated,
            cv=cv,
            content_type=ContentType.objects.get_for_model(type(entry)),
            object_id=entry.pk,
            order=order,
        )
    archive, document = read_archive(populated)
    assert document["resume"]["publications"][0]["title"] == "A book"
    assert document["postulo"]["format"] >= 44

    importer.load(other_user, archive)

    restored = CV.objects.for_user(other_user).get()
    entries = {item.content_type.model: item.item for item in restored.items.all()}
    assert entries["publication"].title == "A book"
    assert entries["publication"].owner == other_user
    assert entries["link"].title == "Site"
    assert entries["link"].owner == other_user
