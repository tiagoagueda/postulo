"""An archive cannot write into another account (#354).

An archive is a file anybody can edit, and the importer used to hand every key of an entry to
a model's constructor: an `owner_id` beat the `owner=` passed beside it, and a `posting_id`
or `application_id` attached a row to somebody else's listing or timeline. Here an archive
whose entries carry such keys, all aimed at a second account, is loaded into a third, and
the second is found as it was.
"""

from __future__ import annotations

import datetime as dt
import io
import json
import zipfile

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from postulo.applications.models import Application, ApplicationEvent, Reminder
from postulo.core import export as export_module
from postulo.core import importer
from postulo.documents.models import CV, UploadedDocument
from postulo.jobs.models import Company, JobPosting
from postulo.resume.models import Experience

pytestmark = pytest.mark.django_db


@pytest.fixture
def victim(other_user):
    """A second account with a listing, an application, a profile, a file and a CV."""
    other_user.profile.headline = "Private headline"
    other_user.profile.save()
    company = Company.objects.create(owner=other_user, name="Umbrella")
    posting = JobPosting.objects.create(
        owner=other_user, company=company, title="Secret listing", description="Private notes"
    )
    application = Application.objects.create(owner=other_user, posting=posting)
    Experience.objects.create(
        owner=other_user, organisation="Umbrella", role="Analyst", start_date=dt.date(2020, 1, 1)
    )
    CV.objects.create(owner=other_user, name="Their CV")
    upload = UploadedDocument.objects.create(
        owner=other_user,
        title="Their CV file",
        file=SimpleUploadedFile("theirs.pdf", b"%PDF-1.7 theirs"),
    )
    return {
        "user": other_user,
        "company": company,
        "posting": posting,
        "application": application,
        "upload": upload,
    }


def snapshot(account) -> dict:
    account.profile.refresh_from_db()
    return {
        "headline": account.profile.headline,
        "avatar": account.profile.avatar.name,
        "profile_pk": account.profile.pk,
        "profile_user": account.profile.user_id,
        "experience": Experience.objects.for_user(account).count(),
        "events": ApplicationEvent.objects.filter(application__owner=account).count(),
        "applications": list(
            Application.objects.for_user(account).values_list("pk", "posting_id", "owner_id")
        ),
        "reminders": Reminder.objects.for_user(account).count(),
        "uploads": list(UploadedDocument.objects.for_user(account).values_list("pk", "file")),
    }


def crafted_archive(source, victim) -> zipfile.ZipFile:
    """What `source` exported, with every entry edited to reach into the victim's account."""
    buffer = export_module.write_archive(source)
    document = json.loads(zipfile.ZipFile(buffer).read(export_module.MANIFEST_NAME))
    their = victim["user"].pk
    reach = {"owner_id": their, "owner": their, "user_id": their, "id": 987654}

    document["account"]["profile"].update(
        {"id": victim["user"].profile.pk, "user_id": their, "avatar": victim["upload"].file.name}
        | {"headline": "Imported headline"}
    )
    for entry in document["resume"]["experience"]:
        entry.update(reach)
    for company in document["companies"]:
        company.update(reach)
        for posting in company["postings"]:
            posting.update(reach)
            for application in posting["applications"]:
                application.update(reach | {"posting_id": victim["posting"].pk})
                for event in application["events"]:
                    event.update(reach | {"application_id": victim["application"].pk})
                for reminder in application["reminders"]:
                    reminder.update(reach | {"application_id": victim["application"].pk})
    for letter in document["documents"]["cover_letters"]:
        letter.update(reach)
    for cv in document["documents"]["cvs"]:
        cv.update(reach)
    for upload in document["documents"]["uploads"]:
        upload.update(reach | {"file": f"media/{victim['upload'].file.name}"})

    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr(export_module.MANIFEST_NAME, json.dumps(document))
    return zipfile.ZipFile(out)


def test_an_archive_cannot_write_into_another_account(user, victim):

    # A source account holding one of everything the crafted entries edit.
    company = Company.objects.create(owner=user, name="Black Mesa")
    posting = JobPosting.objects.create(owner=user, company=company, title="Engineer")
    application = Application.objects.create(owner=user, posting=posting)
    Reminder.objects.create(
        owner=user, application=application, summary="Chase", due_at=timezone.now()
    )
    Experience.objects.create(
        owner=user, organisation="Aperture", role="Dev", start_date=dt.date(2021, 1, 1)
    )
    CV.objects.create(owner=user, name="Mine")
    user.profile.headline = "Source headline"
    user.profile.save()
    archive = crafted_archive(user, victim)
    before = snapshot(victim["user"])

    newcomer = get_user_model().objects.create_user(email="new@example.org", password="x")
    report = importer.load(newcomer, archive)

    assert snapshot(victim["user"]) == before
    assert Application.objects.for_user(victim["user"]).get().posting_id == victim["posting"].pk
    # What came in is the newcomer's, all of it, and points only at the newcomer's rows.
    assert report.applications == 1
    mine = Application.objects.for_user(newcomer).select_related("posting").get()
    assert mine.posting.owner_id == newcomer.pk
    assert mine.posting.title == "Engineer"
    assert not mine.events.exclude(application=mine).exists()
    assert Reminder.objects.for_user(newcomer).get().application_id == mine.pk
    assert Company.objects.for_user(newcomer).get().owner_id == newcomer.pk
    assert Experience.objects.for_user(newcomer).get().owner_id == newcomer.pk
    assert CV.objects.for_user(newcomer).get().owner_id == newcomer.pk
    newcomer.profile.refresh_from_db()
    assert newcomer.profile.headline == "Imported headline"
    assert newcomer.profile.pk != victim["user"].profile.pk
    assert newcomer.profile.user_id == newcomer.pk
    assert not newcomer.profile.avatar
    assert any("not something an archive carries" in line for line in report.skipped)
    # The victim's file was never handed to the newcomer's upload.
    stolen = UploadedDocument.objects.for_user(newcomer)
    assert all(row.file.name != victim["upload"].file.name for row in stolen)
