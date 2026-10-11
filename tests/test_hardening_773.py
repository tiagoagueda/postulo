"""Five Low findings of the security audit of 2026-10-10 (#771, #773, #774, #775, #787)."""

from __future__ import annotations

import pytest
from django.core.files.base import ContentFile
from django.urls import reverse

from postulo.core.files import _lies_under
from postulo.documents.models import CV, RenderedDocument

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------- #773


@pytest.mark.parametrize(
    ("name", "inside"),
    [
        ("documents/7/a.pdf", True),
        ("documents/7/2026/10/a.pdf", True),
        ("documents/70/a.pdf", False),
        ("documents/7/../8/a.pdf", False),
        (r"documents/7/..\8/a.pdf", False),
        ("exports/7/a.zip", False),
    ],
)
def test_a_name_is_under_a_folder_as_a_path_and_not_as_a_string(name, inside):
    assert _lies_under(name, "documents/7/") is inside


def test_a_sent_document_whose_row_names_another_folder_is_not_handed_over(
    client, user, other_user
):
    theirs = RenderedDocument.objects.create(
        owner=user,
        title="Mine",
        kind="cv",
        source=CV.objects.create(owner=user, name="Main"),
        file=ContentFile(b"%PDF-1.7 secret", name="theirs.pdf"),
        checksum="x",
    )
    RenderedDocument.objects.filter(pk=theirs.pk).update(
        file=f"documents/{other_user.pk}/theirs.pdf"
    )
    client.force_login(user)

    assert client.get(reverse("documents:rendered_download", args=[theirs.pk])).status_code == 404
