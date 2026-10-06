"""What a file proves is one of its owner's own entries, whoever asks (#669).

The link from an upload to a career entry crosses a form, a URL filter, an archive and the
API; each is held here to the same rule: another person's entry is never offered, written,
filtered on or shown.
"""

from __future__ import annotations

import pytest
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from postulo.api.models import ApiToken
from postulo.documents import proofs
from postulo.documents.forms import UploadedDocumentForm
from postulo.documents.models import UploadedDocument
from postulo.resume.models import Education

pytestmark = pytest.mark.django_db


def _degree(owner, qualification="BSc"):
    return Education.objects.create(
        owner=owner, institution="University of Aveiro", qualification=qualification
    )


def _held(owner, title="Scan", **extra):
    return UploadedDocument.objects.create(
        owner=owner,
        title=title,
        kind="diploma",
        file=ContentFile(b"%PDF-1.7 x", name="scan.pdf"),
        **extra,
    )


def test_a_forged_form_value_naming_another_persons_entry_writes_nothing(user, other_user):
    theirs = _degree(other_user)
    pdf = SimpleUploadedFile(
        "scan.pdf", b"%PDF-1.7\n1 0 obj<<>>endobj\n%%EOF\n", content_type="application/pdf"
    )
    form = UploadedDocumentForm(
        data={"title": "Scan", "kind": "diploma", "notes": "", "proves": proofs.key_of(theirs)},
        files={"file": pdf},
        user=user,
    )
    assert not form.is_valid()
    assert "proves" in form.errors


def test_a_save_refuses_a_link_to_another_persons_entry(user, other_user):
    theirs = _degree(other_user)
    upload = UploadedDocument(
        owner=user, title="Scan", kind="diploma", file=ContentFile(b"%PDF-1.7 x", name="s.pdf")
    )
    proofs.set_proof(upload, theirs)
    with pytest.raises(ValidationError):
        upload.save()


def test_the_files_list_never_narrows_to_another_persons_entry(client, user, other_user):
    theirs = _degree(other_user)
    _held(other_user, "Their scan")
    _held(user, "My scan")
    client.force_login(user)

    body = client.get(reverse("documents:upload_list"), {"proves": proofs.key_of(theirs)})

    assert "My scan" not in body.content.decode()
    assert "Their scan" not in body.content.decode()


def test_the_api_names_only_the_callers_entry(client, user, other_user):
    degree = _degree(user)
    _held(user, proves_type=ContentType.objects.get_for_model(degree), proves_id=degree.pk)
    _held(other_user)
    _record, raw = ApiToken.issue(user, "Agent", scopes=("read",))

    body = client.get("/api/v1/documents", HTTP_AUTHORIZATION=f"Bearer {raw}").json()

    assert [item["proves"] for item in body["items"]] == [f"education:{degree.pk}"]
