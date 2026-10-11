"""Five Low findings of the security audit of 2026-10-10 (#771, #773, #774, #775, #787)."""

from __future__ import annotations

import pytest
from django.core.files.base import ContentFile
from django.urls import reverse

from postulo.core.files import _type_by_extension
from postulo.documents.models import CV, RenderedDocument

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------- #771


@pytest.mark.parametrize(
    ("name", "type_"),
    [
        ("CV.pdf", "application/pdf"),
        ("data:text/html,x.pdf", "application/pdf"),
        ("data:text/html,hello", "application/octet-stream"),
        ("a.b.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        ("noextension", "application/octet-stream"),
    ],
)
def test_the_media_type_comes_from_the_extension_and_never_from_a_typed_title(name, type_):
    assert _type_by_extension(name) == type_


def test_a_title_that_looks_like_a_data_url_is_served_as_a_pdf(client, user):
    sent = RenderedDocument.objects.create(
        owner=user,
        title="data:text/html,x",
        kind="cv",
        source=CV.objects.create(owner=user, name="Main"),
        file=ContentFile(b"%PDF-1.7 mine", name="sent.pdf"),
        checksum="x",
    )
    client.force_login(user)

    response = client.get(reverse("documents:rendered_download", args=[sent.pk]))
    response.close()

    assert response["Content-Type"] == "application/pdf"
