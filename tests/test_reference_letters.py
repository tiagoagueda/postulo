"""Reference letters: who wrote one, when, and which applications it went with (#666)."""

from __future__ import annotations

import datetime as dt
import json
import zipfile
from io import BytesIO

import pytest
from django.core.files.base import ContentFile
from django.urls import reverse
from django.utils import timezone

from postulo.applications.models import Application, Status
from postulo.core import export as export_module
from postulo.core import importer
from postulo.documents.forms import SendDocumentsForm, UploadedDocumentForm
from postulo.documents.models import ReferenceDelivery, ReferenceLetter, UploadedDocument
from postulo.jobs.models import Company, Contact, JobPosting

pytestmark = pytest.mark.django_db


@pytest.fixture
def referee(user):
    return Contact.objects.create(owner=user, name="Dr Ada Byron", role="Supervisor")


@pytest.fixture
def application(user):
    company = Company.objects.create(owner=user, name="Black Mesa")
    posting = JobPosting.objects.create(owner=user, company=company, title="Research Engineer")
    return Application.objects.create(owner=user, posting=posting, status=Status.APPLIED)


def a_letter(user, referee=None, *, title="Letter from Ada", **fields):
    upload = UploadedDocument.objects.create(
        owner=user,
        title=title,
        kind="reference",
        file=ContentFile(b"%PDF-1.7 ref\n%%EOF", name="r.pdf"),
    )
    ReferenceLetter.objects.create(owner=user, upload=upload, referee=referee, **fields)
    return upload


def the_form(user, data, *, instance=None):
    from django.core.files.uploadedfile import SimpleUploadedFile

    files = None if instance else {"file": SimpleUploadedFile("r.pdf", b"%PDF-1.7 ref\n%%EOF")}
    return UploadedDocumentForm(data, files=files, user=user, instance=instance)


# ---------------------------------------------------------------------- the form


def test_an_upload_of_kind_reference_takes_a_referee_and_dates(user, referee):
    form = the_form(
        user,
        {
            "title": "Ada's letter",
            "kind": "reference",
            "referee": referee.pk,
            "written_on": "2026-03-01",
            "valid_until": "2027-03-01",
            "delivery": "referee",
        },
    )
    assert form.is_valid(), form.errors
    form.instance.owner = user
    upload = form.save()

    letter = upload.reference_letter
    assert letter.referee == referee
    assert letter.written_on == dt.date(2026, 3, 1)
    assert letter.valid_until == dt.date(2027, 3, 1)
    assert letter.delivery == ReferenceDelivery.REFEREE


def test_a_referee_who_is_not_a_contact_yet_is_made_one_without_a_company(user):
    form = the_form(user, {"title": "L", "kind": "reference", "new_referee": " Grace Hopper "})
    assert form.is_valid(), form.errors
    form.instance.owner = user
    upload = form.save()

    assert upload.reference_letter.referee.name == "Grace Hopper"
    assert upload.reference_letter.referee.company is None
    assert upload.reference_letter.referee.owner == user


def test_another_kind_ignores_the_referee_and_dates(user, referee):
    form = the_form(
        user, {"title": "CV", "kind": "cv", "referee": referee.pk, "written_on": "2026-03-01"}
    )
    assert form.is_valid(), form.errors
    form.instance.owner = user
    upload = form.save()

    assert not ReferenceLetter.objects.filter(upload=upload).exists()


def test_a_file_of_another_kind_is_not_asked_about_a_referee(user):
    cv = UploadedDocument.objects.create(
        owner=user, title="CV", kind="cv", file=ContentFile(b"x", name="c.pdf")
    )
    form = UploadedDocumentForm(user=user, instance=cv)
    assert not form.takes_reference
    assert "referee" not in form.fields
    assert UploadedDocumentForm(user=user).takes_reference


def test_a_letter_may_not_expire_before_it_was_written(user):
    form = the_form(
        user,
        {
            "title": "L",
            "kind": "reference",
            "written_on": "2026-03-01",
            "valid_until": "2026-01-01",
        },
    )
    assert not form.is_valid()
    assert "valid_until" in form.errors


def test_a_foreign_contact_is_refused_as_referee(user, other_user):
    stranger = Contact.objects.create(owner=other_user, name="Not Yours")
    form = the_form(user, {"title": "L", "kind": "reference", "referee": stranger.pk})
    assert not form.is_valid()
    assert "referee" in form.errors


def test_changing_the_kind_away_from_reference_drops_the_record(user, referee):
    upload = a_letter(user, referee)
    form = the_form(user, {"title": upload.title, "kind": "cv"}, instance=upload)
    assert form.is_valid(), form.errors
    form.save()
    assert not ReferenceLetter.objects.filter(upload=upload).exists()


def test_the_upload_form_page_shows_the_group_for_a_new_file_and_for_a_letter_only(
    client, user, referee
):
    client.force_login(user)
    assert b"data-reference-fields" in client.get(reverse("documents:upload_create")).content

    cv = UploadedDocument.objects.create(
        owner=user, title="CV", kind="cv", file=ContentFile(b"x", name="c.pdf")
    )
    html = client.get(reverse("documents:upload_update", args=[cv.pk])).content
    assert b"data-reference-fields" not in html

    letter = a_letter(user, referee)
    html = client.get(reverse("documents:upload_update", args=[letter.pk])).content
    assert b"data-reference-fields" in html


def test_deleting_the_upload_deletes_the_record_and_deleting_the_referee_unlinks_it(user, referee):
    upload = a_letter(user, referee)
    referee.delete()

    letter = ReferenceLetter.objects.get(upload=upload)
    assert letter.referee is None
    assert UploadedDocument.objects.filter(pk=upload.pk).exists(), "the file is kept"

    upload.delete()
    assert not ReferenceLetter.objects.exists()


# ------------------------------------------------------- record what you sent


def test_a_letter_is_offered_with_its_referee_and_a_stale_one_says_so(user, referee):
    yesterday = timezone.localdate() - dt.timedelta(days=1)
    a_letter(user, referee, written_on=dt.date(2026, 3, 1), valid_until=yesterday)

    form = SendDocumentsForm(user=user)
    label = next(label for _value, label in form.fields["uploads"].choices if label)

    assert "Dr Ada Byron" in label
    assert "not to be sent after" in label


def test_a_letter_the_referee_sends_is_not_offered_as_an_attachment(
    client, user, referee, application
):
    sent_by_you = a_letter(user, referee, title="Mine to send")
    theirs = a_letter(user, referee, title="Theirs to send", delivery=ReferenceDelivery.REFEREE)

    form = SendDocumentsForm(user=user, application=application)
    offered = set(form.fields["uploads"].queryset.values_list("pk", flat=True))
    assert sent_by_you.pk in offered and theirs.pk not in offered
    assert [letter.upload_id for letter in form.referee_sends] == [theirs.pk]

    client.force_login(user)
    html = client.get(reverse("documents:send", args=[application.pk])).content.decode()
    assert "to be sent by Dr Ada Byron" in html


def test_the_applications_documents_page_names_the_referee_beside_the_file(
    client, user, referee, application
):
    upload = a_letter(user, referee, valid_until=dt.date(2020, 1, 1))
    application.sent_uploads.add(upload)

    client.force_login(user)
    html = client.get(
        reverse("documents:application_documents", args=[application.pk])
    ).content.decode()

    assert "Reference letters" in html
    assert "Dr Ada Byron" in html
    assert "not to send it after" in html


# ---------------------------------------------------------------- the contact


def test_the_contact_page_lists_its_letters_and_where_they_went(client, user, referee, application):
    upload = a_letter(user, referee)
    application.sent_uploads.add(upload)

    client.force_login(user)
    html = client.get(reverse("jobs:contact_update", args=[referee.pk])).content.decode()

    assert "Reference letters they wrote" in html
    assert "Letter from Ada" in html
    assert "Research Engineer" in html


def test_merging_two_contacts_moves_the_letters(user, referee):
    from postulo.jobs import merging

    other = Contact.objects.create(owner=user, name="Ada Byron")
    upload = a_letter(user, other)

    merging.merge_contacts(referee, other)

    assert ReferenceLetter.objects.get(upload=upload).referee == referee


def test_deleting_a_contact_offers_to_delete_the_letters_and_keeps_them_by_default(
    client, user, referee
):
    upload = a_letter(user, referee)
    client.force_login(user)
    url = reverse("jobs:contact_delete", args=[referee.pk])

    page = client.get(url).content.decode()
    assert "1 reference letter they wrote is kept" in page
    assert 'name="delete_letters"' in page

    client.post(url)
    assert ReferenceLetter.objects.get(upload=upload).referee is None


def test_ticking_the_box_deletes_the_letters_with_the_contact(client, user, referee):
    upload = a_letter(user, referee)
    client.force_login(user)
    client.post(reverse("jobs:contact_delete", args=[referee.pk]), {"delete_letters": "1"})

    assert not UploadedDocument.objects.filter(pk=upload.pk).exists()


# --------------------------------------------------------------- search and API


def test_a_letter_is_found_by_its_referees_name(user, referee):
    from postulo.core import search

    a_letter(user, referee, title="Untitled scan")

    found = search.search_uploads(user, "Byron", 10)
    assert [hit.title for hit in found.hits] == ["Untitled scan"]
    assert "Dr Ada Byron" in found.hits[0].subtitle


def test_the_api_returns_the_record_on_an_upload(client, user, referee):
    from postulo.api.schemas import document_out

    class Request:
        def build_absolute_uri(self, path):
            return path

    upload = a_letter(user, referee, written_on=dt.date(2026, 3, 1))
    out = document_out(Request(), UploadedDocument.objects.get(pk=upload.pk), source="upload")

    assert out["reference_letter"] == {
        "referee_id": referee.pk,
        "written_on": dt.date(2026, 3, 1),
        "valid_until": None,
        "delivery": "you",
    }
    plain = UploadedDocument.objects.create(
        owner=user, title="CV", kind="cv", file=ContentFile(b"x", name="c.pdf")
    )
    assert document_out(Request(), plain, source="upload")["reference_letter"] is None


# ----------------------------------------------------------------- the archive


def test_a_letter_survives_the_archive_and_an_older_archive_still_loads(user, other_user, referee):
    a_letter(user, referee, written_on=dt.date(2026, 3, 1), delivery=ReferenceDelivery.REFEREE)
    company = Company.objects.create(owner=user, name="Aperture")
    Contact.objects.create(owner=user, name="Someone", company=company)

    buffer = export_module.write_archive(user)
    importer.load(other_user, zipfile.ZipFile(buffer))

    letter = ReferenceLetter.objects.get(owner=other_user)
    assert letter.referee.name == "Dr Ada Byron" and letter.referee.owner == other_user
    assert letter.written_on == dt.date(2026, 3, 1)
    assert letter.delivery == "referee"
    assert export_module.FORMAT_VERSION >= 44

    # The archive as format 43 wrote it: no record on the upload, which restores as a file.
    with zipfile.ZipFile(export_module.write_archive(user)) as archive:
        document = json.loads(archive.read("postulo.json"))
        members = {name: archive.read(name) for name in archive.namelist()}
    document["postulo"]["format"] = 43
    for entry in document["documents"]["uploads"]:
        entry.pop("reference_letter")
    members["postulo.json"] = json.dumps(document).encode()
    older = BytesIO()
    with zipfile.ZipFile(older, "w") as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    older.seek(0)
    ReferenceLetter.objects.filter(owner=other_user).delete()
    UploadedDocument.objects.filter(owner=other_user).delete()
    importer.load(other_user, zipfile.ZipFile(older), force=True)

    assert UploadedDocument.objects.filter(owner=other_user, kind="reference").count() == 1
    assert not ReferenceLetter.objects.filter(owner=other_user).exists()
